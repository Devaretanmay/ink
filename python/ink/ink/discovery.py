"""Zero-touch discovery, trace ingestion, and decision site detection."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

UUID_REGEX = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
DIGITS_REGEX = re.compile(r"\b\d+\b")
VOLATILE_KEY_PATTERNS = (
    "uuid",
    "guid",
    "nonce",
    "session",
    "timestamp",
    "created_at",
    "request_id",
    "trace_id",
    "span_id",
)


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
class CanonicalTrace:
    timestamp: float
    callsite: str
    state: dict[str, Any]
    choices: list[str]
    choice: str
    latency_ms: float
    cost_usd: float
    model: str
    outcome: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_canonical(record: dict[str, Any]) -> CanonicalTrace | None:
    callsite = record.get("callsite") or record.get("site_name") or record.get("template_name")
    if not callsite:
        return None
    state = None
    if isinstance(record.get("state"), dict):
        state = record["state"]
    else:
        for k in ("state", "query", "input", "prompt", "arguments", "messages"):
            if k in record and record[k] is not None:
                state = {k: record[k]} if not isinstance(record[k], dict) else record[k]
                break
    if state is None:
        state = {"input": {}}
    choice = str(
        record.get("choice") or record.get("response") or record.get("action") or ""
    ).strip()
    latency = float(
        record.get("latency_ms") or record.get("elapsed_ms") or record.get("duration_ms") or 150.0
    )
    cost = float(record.get("cost_usd") or record.get("cost") or 0.0005)
    model = str(record.get("model") or "unknown")
    timestamp = float(record.get("timestamp") or 0.0)
    choices = list(record.get("choices") or [])
    outcome = record.get("outcome")
    if outcome is not None and not isinstance(outcome, dict):
        outcome = {"quality": 1.0 if outcome in (1, True, "success") else 0.0}
    elif outcome is None and "success" in record:
        outcome = {"quality": 1.0 if record["success"] in (1, True) else 0.0}
    return CanonicalTrace(
        timestamp=timestamp,
        callsite=str(callsite),
        state=state,
        choices=choices,
        choice=choice,
        latency_ms=latency,
        cost_usd=cost,
        model=model,
        outcome=outcome,
        raw=record,
    )


def normalize_opentelemetry(record: dict[str, Any]) -> CanonicalTrace | None:
    attrs = record.get("attributes")
    if not isinstance(attrs, dict) and "name" not in record:
        return None
    attrs = attrs or {}
    callsite = (
        attrs.get("callsite")
        or attrs.get("gen_ai.system")
        or attrs.get("gen_ai.operation.name")
        or record.get("name")
        or "otel_span"
    )
    prompt = attrs.get("gen_ai.prompt") or attrs.get("prompt") or attrs.get("state")
    if isinstance(prompt, dict):
        state = prompt
    elif isinstance(prompt, str):
        state = {"prompt": prompt}
    else:
        state = {
            k: v
            for k, v in attrs.items()
            if not k.startswith("gen_ai.response") and not k.startswith("llm.")
        }
    choice = str(
        attrs.get("gen_ai.completion")
        or attrs.get("choice")
        or attrs.get("response")
        or record.get("status", {}).get("message")
        or ""
    ).strip()
    start_ns = record.get("start_time_unix_nano") or record.get("startTimeUnixNano")
    end_ns = record.get("end_time_unix_nano") or record.get("endTimeUnixNano")
    if (
        start_ns
        and end_ns
        and isinstance(start_ns, (int, float))
        and isinstance(end_ns, (int, float))
    ):
        latency_ms = (end_ns - start_ns) / 1_000_000.0
        timestamp = start_ns / 1_000_000_000.0
    else:
        latency_ms = float(attrs.get("duration_ms") or record.get("duration_ms") or 120.0)
        timestamp = float(record.get("timestamp") or 0.0)
    cost = float(attrs.get("llm.cost") or attrs.get("gen_ai.cost") or 0.0004)
    model = str(attrs.get("gen_ai.request.model") or attrs.get("model") or "otel_model")
    status = record.get("status", {})
    status_code = status.get("code") if isinstance(status, dict) else None
    outcome = None
    if status_code is not None:
        outcome = {"quality": 1.0 if status_code in (0, 1) else 0.0, "verifier": "otel_status"}
    elif "outcome" in attrs:
        outcome = (
            attrs["outcome"]
            if isinstance(attrs["outcome"], dict)
            else {"quality": float(attrs["outcome"])}
        )
    return CanonicalTrace(
        timestamp=timestamp,
        callsite=str(callsite),
        state=state,
        choices=[],
        choice=choice,
        latency_ms=round(latency_ms, 2),
        cost_usd=cost,
        model=model,
        outcome=outcome,
        raw=record,
    )


def normalize_langsmith(record: dict[str, Any]) -> CanonicalTrace | None:
    if "inputs" not in record and "outputs" not in record and "run_type" not in record:
        return None
    callsite = record.get("name") or record.get("run_type") or "langsmith_run"
    inputs = record.get("inputs")
    state = inputs if isinstance(inputs, dict) else {"input": inputs}
    outputs = record.get("outputs") or {}
    choice = ""
    if isinstance(outputs, dict):
        for k in ("choice", "output", "action", "text", "response", "content"):
            if k in outputs:
                val = outputs[k]
                choice = str(val if not isinstance(val, dict) else json.dumps(val)).strip()
                break
        if not choice and outputs:
            first_val = next(iter(outputs.values()))
            choice = str(
                first_val if not isinstance(first_val, dict) else json.dumps(first_val)
            ).strip()
    else:
        choice = str(outputs).strip()
    start = record.get("start_time") or record.get("startTime")
    end = record.get("end_time") or record.get("endTime")
    timestamp = float(start) if isinstance(start, (int, float)) else 0.0
    if start and end and isinstance(start, (int, float)) and isinstance(end, (int, float)):
        latency_ms = (end - start) * 1000.0
    else:
        latency_ms = float(record.get("latency_ms") or 140.0)
    extra = record.get("extra") or {}
    meta = extra.get("metadata") or {}
    model = str(meta.get("model") or record.get("model") or "langsmith_llm")
    cost = float(meta.get("total_cost") or record.get("total_cost") or 0.0005)
    feedbacks = record.get("feedback") or []
    outcome = None
    if isinstance(feedbacks, list) and feedbacks:
        fb = feedbacks[0]
        score = fb.get("score") if isinstance(fb, dict) else None
        if score is not None:
            outcome = {"quality": float(score), "verifier": "langsmith_feedback"}
    return CanonicalTrace(
        timestamp=timestamp,
        callsite=str(callsite),
        state=state,
        choices=[],
        choice=choice,
        latency_ms=round(latency_ms, 2),
        cost_usd=cost,
        model=model,
        outcome=outcome,
        raw=record,
    )


def normalize_litellm(record: dict[str, Any]) -> CanonicalTrace | None:
    if (
        "messages" not in record
        and "response_cost" not in record
        and "litellm_call_id" not in record
    ):
        return None
    meta = record.get("metadata") or {}
    callsite = (
        meta.get("callsite")
        or meta.get("custom_llm_provider")
        or record.get("model")
        or "litellm_call"
    )
    messages = record.get("messages") or record.get("input") or {}
    state = (
        {"messages": messages}
        if isinstance(messages, list)
        else (messages if isinstance(messages, dict) else {"input": str(messages)})
    )
    choice = ""
    resp = record.get("response") or {}
    if isinstance(resp, dict):
        choices = resp.get("choices")
        if isinstance(choices, list) and choices:
            c0 = choices[0]
            if isinstance(c0, dict):
                msg = c0.get("message") or {}
                choice = str(msg.get("content") or c0.get("text") or "").strip()
    if not choice:
        choice = str(record.get("output") or record.get("response") or "").strip()
    latency_ms = float(record.get("response_time_ms") or record.get("latency_ms") or 130.0)
    cost = float(record.get("response_cost") or record.get("spend") or 0.0004)
    model = str(record.get("model") or "litellm_model")
    timestamp = float(record.get("timestamp") or 0.0)
    status = record.get("status")
    outcome = (
        {"quality": 1.0 if status == "success" else 0.0, "verifier": "litellm_status"}
        if status
        else None
    )
    return CanonicalTrace(
        timestamp=timestamp,
        callsite=str(callsite),
        state=state,
        choices=[],
        choice=choice,
        latency_ms=round(latency_ms, 2),
        cost_usd=cost,
        model=model,
        outcome=outcome,
        raw=record,
    )


def normalize_record(record: dict[str, Any]) -> CanonicalTrace | None:
    if not isinstance(record, dict):
        return None
    if "callsite" in record or "site_name" in record or "template_name" in record:
        return normalize_canonical(record)
    if "attributes" in record or "start_time_unix_nano" in record:
        return normalize_opentelemetry(record)
    if "inputs" in record and ("outputs" in record or "run_type" in record):
        return normalize_langsmith(record)
    if "response_cost" in record or "litellm_call_id" in record:
        return normalize_litellm(record)
    return normalize_canonical(record)


def stream_traces(source: str | Path) -> Iterator[CanonicalTrace]:
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"Trace file not found: {source}")
    with path.open("r", encoding="utf-8") as f:
        first_char = f.read(1)
        f.seek(0)
        if first_char == "[":
            payload = json.load(f)
            for item in payload:
                norm = normalize_record(item)
                if norm is not None:
                    yield norm
        else:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    norm = normalize_record(raw)
                    if norm is not None:
                        yield norm
                except json.JSONDecodeError:
                    continue


CORE_PAYLOAD_FIELDS = {
    "query",
    "prompt",
    "text",
    "message",
    "messages",
    "content",
    "input",
    "question",
    "state",
}


def detect_volatile_fields(states: list[dict[str, Any]]) -> list[str]:
    if not states:
        return []
    field_values: dict[str, list[Any]] = defaultdict(list)
    for s in states:
        for k, v in s.items():
            field_values[k].append(v)
    if len(field_values) <= 1:
        return []
    volatile = []
    total = len(states)
    for field, vals in field_values.items():
        lower_field = field.lower()
        if lower_field in CORE_PAYLOAD_FIELDS:
            continue
        if any(pat in lower_field for pat in VOLATILE_KEY_PATTERNS):
            volatile.append(field)
            continue
        str_vals = [str(v).strip() for v in vals if v is not None]
        if not str_vals:
            continue
        if sum(1 for v in str_vals if UUID_REGEX.match(v)) / len(str_vals) >= 0.5:
            volatile.append(field)
            continue
        is_id_like = any(lower_field.endswith(sfx) for sfx in ("_id", "id", "_key", "key", "_ref"))
        if is_id_like and len(set(str_vals)) / len(str_vals) >= 0.95 and total >= 20:
            volatile.append(field)
    return sorted(set(volatile))


def detect_templates(states: list[dict[str, Any]]) -> tuple[float, float]:
    if not states:
        return 0.0, 0.0
    raw_keys = [json.dumps(s, sort_keys=True) for s in states]
    raw_repeat = 1.0 - (len(set(raw_keys)) / len(raw_keys))
    templated_keys = []
    for s in states:
        clean = {}
        for k, v in s.items():
            if isinstance(v, str):
                clean[k] = DIGITS_REGEX.sub("{id}", v)
            else:
                clean[k] = v
        templated_keys.append(json.dumps(clean, sort_keys=True))
    templated_repeat = 1.0 - (len(set(templated_keys)) / len(templated_keys))
    return round(raw_repeat, 4), round(templated_repeat, 4)


def infer_state_schema(states: list[dict[str, Any]], exclude_fields: set[str]) -> dict[str, str]:
    types: dict[str, set[str]] = defaultdict(set)
    for s in states:
        for k, v in s.items():
            if k in exclude_fields:
                continue
            if isinstance(v, bool):
                types[k].add("boolean")
            elif isinstance(v, int):
                types[k].add("integer")
            elif isinstance(v, float):
                types[k].add("float")
            elif isinstance(v, dict):
                types[k].add("object")
            elif isinstance(v, list):
                types[k].add("array")
            else:
                types[k].add("string")
    schema = {}
    for k, tset in sorted(types.items()):
        if "string" in tset:
            schema[k] = "string"
        elif "float" in tset:
            schema[k] = "float"
        elif "integer" in tset:
            schema[k] = "integer"
        elif "boolean" in tset:
            schema[k] = "boolean"
        elif "object" in tset:
            schema[k] = "object"
        elif "array" in tset:
            schema[k] = "array"
        else:
            schema[k] = "string"
    return schema


def assess_verifier_readiness(rows: list[CanonicalTrace]) -> tuple[str, float, list[str]]:
    total = len(rows)
    if not total:
        return "no_verifier", 0.0, []
    verifiable = sum(
        1 for r in rows if r.outcome is not None and r.outcome.get("quality") is not None
    )
    coverage = round(verifiable / total, 4)
    suggested = []
    for r in rows:
        if r.outcome and r.outcome.get("verifier"):
            v = str(r.outcome["verifier"])
            if v not in suggested:
                suggested.append(v)
    if not suggested:
        sample_state = rows[0].state
        for k in ("status", "status_code", "exit_code", "error_code", "http_status"):
            if k in sample_state:
                suggested.append(f"state.{k}")
    if coverage >= 0.80:
        readiness = "verifier_ready"
    elif coverage >= 0.20:
        readiness = "verifier_partial"
    else:
        readiness = "no_verifier"
    return readiness, coverage, suggested


def generate_snippet(
    site_name: str,
    schema: dict[str, str],
    choices: list[str],
    volatile_fields: list[str] | None = None,
) -> str:
    schema_str = json.dumps(schema or {"input": "string"}, indent=8).rstrip("}").strip() + "\n    }"
    choices_str = ", ".join(repr(c) for c in (choices if choices else ["action_1", "action_2"]))
    vol_lines = ""
    if volatile_fields:
        vol_list = repr(sorted(volatile_fields))
        vol_lines = (
            f"# Note: Suggested exclusions detected in telemetry: {vol_list}\n"
            f"# Review whether these fields are semantically critical before excluding:\n"
            f"# state = {{k: v for k, v in raw_state.items() if k not in set({vol_list})}}\n\n"
        )
    return (
        f"{vol_lines}"
        f"site = DecisionSite(\n"
        f'    name="{site_name}",\n'
        f"    state_schema={schema_str},\n"
        f"    choices=({choices_str},),\n"
        f")\n\n"
        f"result = ml.decide(site=site.name, state=state, fallback=current_model_call)\n"
        f"# Execute action and record outcome receipt:\n"
        f"ml.record_outcome(\n"
        f"    result.decision_id,\n"
        f"    quality=1.0 if outcome_verified else 0.0,\n"
        f'    verifier="task_verifier",\n'
        f'    verifier_version="1",\n'
        f'    evidence={{"status": "verified"}},\n'
        f")"
    )


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
    templated_repetition_rate: float = 0.0
    categorical_stability: float = 0.0
    verifier_readiness: str = "no_verifier"
    verifier_coverage: float = 0.0
    suggested_verifiers: list[str] | None = None
    volatile_fields: list[str] | None = None
    suggested_state_schema: dict[str, str] | None = None
    break_even_decisions: int = 999999
    qualification_cost_usd: float = 0.0
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def discover_from_stream(
    trace_stream: Iterator[CanonicalTrace], max_rows: int | None = None
) -> list[CandidateSite]:
    site_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "total_calls": 0,
            "choices": Counter(),
            "cost_sum": 0.0,
            "latencies": [],
            "min_ts": float("inf"),
            "max_ts": 0.0,
            "verifiable_count": 0,
            "verifiers": set(),
            "state_samples": [],
            "state_hashes": set(),
        }
    )
    count = 0
    for trace in trace_stream:
        count += 1
        st = site_stats[trace.callsite]
        st["total_calls"] += 1
        if trace.choice:
            st["choices"][trace.choice] += 1
        st["cost_sum"] += trace.cost_usd
        if len(st["latencies"]) < 2000:
            st["latencies"].append(trace.latency_ms)
        if trace.timestamp > 0:
            st["min_ts"] = min(st["min_ts"], trace.timestamp)
            st["max_ts"] = max(st["max_ts"], trace.timestamp)
        if trace.outcome is not None and trace.outcome.get("quality") is not None:
            st["verifiable_count"] += 1
            if trace.outcome.get("verifier"):
                st["verifiers"].add(str(trace.outcome["verifier"]))
        if len(st["state_samples"]) < 2000:
            st["state_samples"].append(trace.state)
        if len(st["state_hashes"]) < 50000:
            st["state_hashes"].add(hash(json.dumps(trace.state, sort_keys=True)))
        if max_rows and count >= max_rows:
            break

    results: list[CandidateSite] = []
    for site_key, st in site_stats.items():
        total_calls = st["total_calls"]
        if not total_calls:
            continue
        states = st["state_samples"]
        raw_rep, templated_rep = detect_templates(states)
        volatile = detect_volatile_fields(states)
        volatile_set = set(volatile)
        schema = infer_state_schema(states, volatile_set)
        clean_keys = []
        for s in states:
            clean = {k: v for k, v in s.items() if k not in volatile_set}
            clean_keys.append(json.dumps(clean, sort_keys=True))
        sample_count = len(clean_keys)
        clean_rep = round(1.0 - (len(set(clean_keys)) / sample_count), 4) if sample_count else 0.0
        effective_rep = clean_rep
        choice_counts = st["choices"]
        output_entropy = _shannon_entropy(choice_counts)
        distinct_choices = [c for c, _ in choice_counts.most_common(10)]
        num_choices = len(choice_counts)
        choices_total = sum(choice_counts.values())
        top3_count = sum(cnt for _, cnt in choice_counts.most_common(3))
        cat_stability = round(top3_count / choices_total, 4) if choices_total else 0.0
        v_cov = round(st["verifiable_count"] / total_calls, 4)
        if v_cov >= 0.80:
            readiness = "verifier_ready"
        elif v_cov >= 0.20:
            readiness = "verifier_partial"
        else:
            readiness = "no_verifier"
        suggested_v = sorted(st["verifiers"])
        if not suggested_v and states:
            sample_state = states[0]
            for k in ("status", "status_code", "exit_code", "error_code", "http_status"):
                if k in sample_state:
                    suggested_v.append(f"state.{k}")
        latencies = sorted(st["latencies"])
        p50_lat = latencies[len(latencies) // 2] if latencies else 150.0
        avg_cost = st["cost_sum"] / total_calls
        if st["max_ts"] > st["min_ts"]:
            span_days = max(0.01, (st["max_ts"] - st["min_ts"]) / 86400.0)
            daily_calls = total_calls / span_days
        else:
            daily_calls = float(total_calls)
        call_frequency = round(daily_calls, 2)
        bounded_choices = max(2, min(num_choices, 10))
        qual_cost_decisions = 50 * bounded_choices
        qual_cost_usd = round(qual_cost_decisions * avg_cost, 4)
        if effective_rep > 0.01:
            break_even = int(math.ceil(qual_cost_decisions / (effective_rep * 0.95)))
        else:
            break_even = 999999
        avoided_annual = (daily_calls * 365) * (effective_rep * 0.95)
        gross_savings = avoided_annual * avg_cost
        net_annual = round(max(0.0, gross_savings - qual_cost_usd), 2)
        all_choices = list(choice_counts.keys())
        avg_choice_len = sum(len(c) for c in all_choices) / len(all_choices) if all_choices else 0
        if total_calls < 20:
            rec = "investigate"
            reason = f"Low observation count ({total_calls}); collect more trace data."
        elif num_choices > 15 or output_entropy > 3.8 or avg_choice_len > 80:
            rec = "ignore"
            reason = (
                f"High output entropy ({output_entropy:.2f} bits, {num_choices} choices, "
                f"avg length {avg_choice_len:.0f} chars); free-form generation is not bounded."
            )
        elif effective_rep < 0.15:
            rec = "ignore"
            reason = f"Low repetition rate ({effective_rep:.1%}); fast paths would rarely trigger."
        elif readiness == "no_verifier" and total_calls >= 50:
            rec = "investigate"
            reason = (
                "No factual outcome verifier detected in traces; "
                "shadow qualification requires independent verification."
            )
        elif net_annual < 20.0 and total_calls >= 50:
            rec = "investigate"
            reason = (
                f"Marginal economic savings (${net_annual:.2f}/yr); monitor for higher traffic."
            )
        else:
            rec = "compile"
            reason = (
                f"Strong candidate: {effective_rep:.1%} repetition, "
                f"bounded choices ({num_choices}), break-even in ~{break_even} decisions."
            )
        snippet = generate_snippet(site_key, schema, distinct_choices, volatile)
        verifiability = v_cov if readiness != "no_verifier" else 0.0
        candidate = CandidateSite(
            site_name=site_key,
            call_frequency=call_frequency,
            repetition_rate=effective_rep,
            output_entropy=output_entropy,
            verifiability=verifiability,
            estimated_annual_savings=net_annual,
            recommendation=rec,
            reason=reason,
            total_calls=total_calls,
            unique_inputs=len(st["state_hashes"]),
            choices=distinct_choices,
            p50_latency_ms=round(p50_lat, 2),
            avg_cost=round(avg_cost, 6),
            templated_repetition_rate=templated_rep,
            categorical_stability=cat_stability,
            verifier_readiness=readiness,
            verifier_coverage=v_cov,
            suggested_verifiers=suggested_v,
            volatile_fields=volatile,
            suggested_state_schema=schema,
            break_even_decisions=break_even,
            qualification_cost_usd=qual_cost_usd,
            snippet=snippet,
        )
        results.append(candidate)
    results.sort(
        key=lambda s: (s.recommendation == "compile", s.estimated_annual_savings), reverse=True
    )
    return results


def discover_from_traces(traces: list[dict[str, Any] | CanonicalTrace]) -> list[CandidateSite]:
    def _gen():
        for item in traces:
            if isinstance(item, CanonicalTrace):
                yield item
            else:
                norm = normalize_record(item)
                if norm is not None:
                    yield norm

    return discover_from_stream(_gen())


def discover_from_file(path: str | Path, max_rows: int | None = None) -> list[CandidateSite]:
    return discover_from_stream(stream_traces(path), max_rows=max_rows)
