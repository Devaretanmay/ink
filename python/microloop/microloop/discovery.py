"""Discovery tool for identifying compilable DecisionSites from agent execution traces."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def _shannon_entropy(counts: Counter) -> float:
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    entropy = 0.0
    for count in counts.values():
        if count > 0:
            p = count / total
            entropy -= p * math.log2(p)
    return round(entropy, 4)


@dataclass
class CandidateSite:
    site_name: str
    call_frequency: float
    repetition_rate: float
    output_entropy: float
    verifiability: float
    estimated_annual_savings: float
    recommendation: str
    reason: str
    total_calls: int
    unique_inputs: int
    choices: list[str]
    p50_latency_ms: float
    avg_cost: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extract_site_key(record: dict[str, Any]) -> str:
    for field in ("site_name", "decision_site", "template_name", "tool_name", "name"):
        if record.get(field):
            return str(record[field])
    if "prompt" in record and isinstance(record["prompt"], str):
        first_line = record["prompt"].strip().split("\n")[0][:40]
        return f"prompt_{first_line}"
    return "default_agent_site"


def _extract_input_key(record: dict[str, Any]) -> str:
    for field in ("state", "input", "query", "arguments", "messages"):
        if field in record:
            val = record[field]
            return json.dumps(val, sort_keys=True, separators=(",", ":"))
    return json.dumps(record.get("prompt", ""), sort_keys=True)


def _extract_choice(record: dict[str, Any]) -> str:
    for field in ("choice", "action", "tool", "output", "response"):
        if field in record:
            return str(record[field]).strip()
    return "unknown"


def discover_from_traces(traces: list[dict[str, Any]]) -> list[CandidateSite]:
    """Analyze raw agent execution traces and report candidate DecisionSites."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in traces:
        key = _extract_site_key(t)
        grouped[key].append(t)

    results: list[CandidateSite] = []
    for site_key, rows in grouped.items():
        total_calls = len(rows)
        inputs = [_extract_input_key(r) for r in rows]
        unique_inputs = len(set(inputs))
        unique_ratio = unique_inputs / total_calls if total_calls else 1.0
        repetition_rate = round(1.0 - unique_ratio, 4)

        choices = [_extract_choice(r) for r in rows]
        choice_counts = Counter(choices)
        output_entropy = _shannon_entropy(choice_counts)

        verifiable_rows = [
            r for r in rows
            if "outcome" in r or "success" in r or "status" in r or "quality" in r
        ]
        if verifiable_rows:
            successes = sum(
                1 for r in verifiable_rows
                if r.get("outcome") in (1, True, "success")
                or r.get("success") in (1, True)
                or r.get("quality") in (1, True)
                or (isinstance(r.get("outcome"), dict) and r["outcome"].get("quality") == 1)
            )
            verifiability = round(successes / len(verifiable_rows), 4)
        else:
            verifiability = 0.0

        latencies = [
            float(r.get("elapsed_ms") or r.get("duration_ms") or 150.0)
            for r in rows
        ]
        sorted_lats = sorted(latencies)
        p50_lat = sorted_lats[len(sorted_lats) // 2] if sorted_lats else 150.0

        costs = [
            float(r.get("cost") or r.get("usage", {}).get("cost") or 0.002)
            for r in rows
        ]
        avg_cost = sum(costs) / len(costs) if costs else 0.002

        times = [r.get("timestamp") for r in rows if isinstance(r.get("timestamp"), (int, float))]
        if len(times) >= 2:
            span_days = max(0.01, (max(times) - min(times)) / 86400.0)
            daily_calls = total_calls / span_days
        else:
            daily_calls = float(total_calls)
        call_frequency = round(daily_calls, 2)

        distinct_choices = list(choice_counts.keys())
        num_choices = len(distinct_choices)
        qual_cost = 50 * max(2, min(num_choices, 10)) * avg_cost
        avoided_annual = (daily_calls * 365) * (repetition_rate * 0.95)
        gross_savings = avoided_annual * avg_cost
        net_annual_savings = round(max(0.0, gross_savings - qual_cost), 2)

        if total_calls < 20:
            rec = "investigate"
            reason = f"Low observation count ({total_calls}); collect more trace data."
        elif len(distinct_choices) > 25 or output_entropy > 4.5:
            rec = "ignore"
            reason = (
                f"High output entropy ({output_entropy:.2f} bits, "
                f"{len(distinct_choices)} choices); free-form generation is not a bounded decision."
            )
        elif repetition_rate < 0.15:
            rec = "ignore"
            reason = (
                f"Low repetition rate ({repetition_rate:.1%}); fast paths would rarely trigger."
            )
        elif verifiability < 0.70 and verifiable_rows:
            rec = "investigate"
            reason = (
                f"Weak verifiability ({verifiability:.1%}); "
                "verifier reliability must be improved before shadow qualification."
            )
        elif net_annual_savings < 25.0:
            rec = "investigate"
            reason = (
                f"Marginal economic savings (${net_annual_savings:.2f}/yr); "
                "monitor for traffic scaling."
            )
        else:
            rec = "compile"
            payoff_calls = int(qual_cost / (avg_cost * max(0.01, repetition_rate)))
            reason = (
                f"Strong candidate: {repetition_rate:.1%} repeat rate, "
                f"bounded choices ({len(distinct_choices)}), amortizes in ~{payoff_calls} calls."
            )

        results.append(
            CandidateSite(
                site_name=site_key,
                call_frequency=call_frequency,
                repetition_rate=repetition_rate,
                output_entropy=output_entropy,
                verifiability=verifiability,
                estimated_annual_savings=net_annual_savings,
                recommendation=rec,
                reason=reason,
                total_calls=total_calls,
                unique_inputs=unique_inputs,
                choices=distinct_choices[:10],
                p50_latency_ms=round(p50_lat, 2),
                avg_cost=round(avg_cost, 6),
            )
        )

    results.sort(key=lambda s: s.estimated_annual_savings, reverse=True)
    return results


def discover_from_file(path: str | Path) -> list[CandidateSite]:
    """Parse JSON or JSONL file and discover candidate DecisionSites."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Trace file not found: {path}")

    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return []

    traces: list[dict[str, Any]] = []
    if text.startswith("["):
        traces = json.loads(text)
    else:
        for line in text.splitlines():
            line = line.strip()
            if line:
                traces.append(json.loads(line))
    return discover_from_traces(traces)
