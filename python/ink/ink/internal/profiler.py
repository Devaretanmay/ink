"""Site profiler: trustworthy economics and advisory summaries from local decision history."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from typing import Any


def _percentiles(values, points=(50, 95)):
    if not values:
        return {f"p{p}": None for p in points}
    ordered = sorted(values)
    out = {}
    for p in points:
        rank = min(len(ordered) - 1, int(len(ordered) * p / 100))
        out[f"p{p}"] = ordered[rank]
    return out


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


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    return {text[i : i + n] for i in range(max(1, len(text) - n + 1))}


def _estimate_semantic_clusters(unique_state_strings: list[str]) -> int:
    if not unique_state_strings:
        return 0
    sampled = unique_state_strings[:100]
    ngrams_list = [_char_ngrams(s) for s in sampled]
    clusters: list[set[str]] = []
    for ng in ngrams_list:
        matched = False
        for rep in clusters:
            intersection = len(ng & rep)
            union = len(ng | rep)
            if union > 0 and (intersection / union) >= 0.65:
                matched = True
                break
        if not matched:
            clusters.append(ng)
    scale = len(unique_state_strings) / len(sampled)
    return max(1, int(round(len(clusters) * scale)))


class SiteProfile(dict):
    """Advisory site profile and economic assessment."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.__dict__.update(kwargs)

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'SiteProfile' object has no attribute {name!r}") from None

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value
        self.__dict__[name] = value

    def to_dict(self) -> dict[str, Any]:
        return dict(self)


def profile_history(rows: list[dict[str, Any]]) -> SiteProfile:
    observations = len(rows)
    tasks = [r.get("task") for r in rows]
    states = [r.get("state") for r in rows]

    def key(s):
        return json.dumps(s, sort_keys=True, separators=(",", ":")) if s is not None else ""

    state_keys = [key(s) for s in states]
    state_counts = Counter(state_keys)
    choices = Counter(r.get("choice") for r in rows if r.get("choice") is not None)
    sources = Counter(r.get("source") for r in rows if r.get("source") is not None)
    reasons = Counter(r.get("reason") for r in rows if r.get("reason"))

    missing = sum(1 for r in rows if r.get("outcome") is None)
    with_outcome = [r for r in rows if r.get("outcome") is not None]
    successful = sum(1 for r in with_outcome if r["outcome"].get("quality") == 1)
    unsuccessful = len(with_outcome) - successful

    usage = {}
    for field in ("model_calls", "input_tokens", "output_tokens", "cost", "request_attempts"):
        known = [
            r["usage"][field]
            for r in rows
            if isinstance(r.get("usage"), dict) and r["usage"].get(field) is not None
        ]
        usage[field] = sum(known) if known else None
    usage["rows_with_usage"] = sum(
        1
        for r in rows
        if isinstance(r.get("usage"), dict)
        and any(
            r["usage"].get(k) is not None
            for k in ("model_calls", "input_tokens", "output_tokens", "cost", "request_attempts")
        )
    )

    elapsed = [r["elapsed"] for r in rows if isinstance(r.get("elapsed"), (int, float))]
    lat = _percentiles(elapsed)
    fallback_elapsed = [
        r["elapsed"]
        for r in rows
        if r.get("source") == "fallback" and isinstance(r.get("elapsed"), (int, float))
    ]
    fb_lat = _percentiles(fallback_elapsed) if fallback_elapsed else lat
    fallback_p50_ms = round((fb_lat["p50"] or 0.0) * 1000.0, 3)

    fb_costs = [
        r["usage"]["cost"]
        for r in rows
        if r.get("source") == "fallback"
        and isinstance(r.get("usage"), dict)
        and r["usage"].get("cost") is not None
    ]
    fallback_cost_per_decision = (
        round(sum(fb_costs) / len(fb_costs), 6) if fb_costs else 0.0
    )

    distinct_tasks = len(set(tasks)) if rows else 0
    times = [r["created"] for r in rows if r.get("created") is not None]
    duration = max(times) - min(times) if times else 0.0

    unique_states = len(state_counts)
    unique_state_ratio = round(unique_states / observations, 4) if observations else 0.0
    exact_repeat_rate = round(1.0 - unique_state_ratio, 4) if observations else 0.0
    novelty_rate = unique_state_ratio

    unique_keys = list(state_counts.keys())
    semantic_clusters = _estimate_semantic_clusters(unique_keys)
    raw_sem_repeat = 1.0 - (semantic_clusters / observations) if observations else 0.0
    semantic_repeat_rate = round(max(exact_repeat_rate, raw_sem_repeat), 4)

    choice_entropy = _shannon_entropy(choices)
    outcome_completeness = round(len(with_outcome) / observations, 4) if observations else 0.0
    verifier_quality = round(successful / len(with_outcome), 4) if with_outcome else 0.0

    choice_by_state: dict[str, list[str]] = defaultdict(list)
    for sk, r in zip(state_keys, rows, strict=True):
        ch = r.get("choice")
        if ch is not None:
            choice_by_state[sk].append(ch)
    shifts = sum(
        sum(1 for i in range(1, len(chs)) if chs[i] != chs[i - 1])
        for chs in choice_by_state.values()
    )
    repeated_instances = max(1, observations - unique_states)
    policy_volatility = (
        round(shifts / repeated_instances, 4) if observations > unique_states else 0.0
    )

    num_choices = max(2, len(choices))
    qualification_cost_decisions = 50 * num_choices
    effective_repeat = max(exact_repeat_rate, semantic_repeat_rate)
    if effective_repeat > 0.01:
        break_even_decisions = int(
            math.ceil(qualification_cost_decisions / (effective_repeat * 0.95))
        )
    else:
        break_even_decisions = 999999

    recommendation = "strong_candidate"
    reasons_list: list[str] = []

    if observations < 50:
        recommendation = "needs_more_data"
        reasons_list.append(
            f"Insufficient observations ({observations} < 50) to evaluate economic viability."
        )
    elif unique_state_ratio > 0.85:
        recommendation = "poor_repetition"
        reasons_list.append(
            f"High state entropy / unique state ratio ({unique_state_ratio:.2%}) "
            "exceeds 85% threshold."
        )
    elif outcome_completeness < 0.80:
        recommendation = "weak_verifier"
        reasons_list.append(
            f"Outcome feedback completeness ({outcome_completeness:.2%}) is below 80% threshold."
        )
    elif policy_volatility > 0.15:
        recommendation = "high_volatility"
        reasons_list.append(
            f"Policy volatility ({policy_volatility:.2%}) exceeds 15% threshold; "
            "frequent policy changes cause demotion."
        )
    elif fallback_p50_ms < 10.0 and fallback_cost_per_decision < 0.0001:
        recommendation = "low_economic_value"
        reasons_list.append(
            f"Fallback latency ({fallback_p50_ms:.1f}ms < 10ms) and cost "
            f"(${fallback_cost_per_decision:.6f} < $0.0001) yield negligible savings."
        )
    else:
        reasons_list.append(
            f"High repetition ({exact_repeat_rate:.2%} exact, {semantic_repeat_rate:.2%} sem), "
            f"verifier completeness ({outcome_completeness:.2%}), "
            f"bounded volatility ({policy_volatility:.2%}), amortizes in ~{break_even_decisions} d."
        )

    return SiteProfile(
        observations=observations,
        observation_span_seconds=duration,
        decisions_per_second=(observations - 1) / duration if duration else None,
        distinct_tasks=distinct_tasks,
        single_use_tasks=distinct_tasks == observations if rows else True,
        unique_states=unique_states,
        unique_state_ratio=unique_state_ratio,
        repetition_rate=1 - unique_states / observations if observations else 0.0,
        exact_repeat_rate=exact_repeat_rate,
        semantic_repeat_rate=semantic_repeat_rate,
        novelty_rate=novelty_rate,
        choice_entropy=choice_entropy,
        most_common_states=state_counts.most_common(5),
        choices=dict(choices),
        sources=dict(sources),
        fallback_reasons=dict(reasons),
        outcome_completeness=outcome_completeness,
        missing_outcomes=missing,
        successful_outcomes=successful,
        unsuccessful_outcomes=unsuccessful,
        verifier_quality=verifier_quality,
        usage=usage,
        elapsed_p50=lat["p50"],
        elapsed_p95=lat["p95"],
        latency_by_source={
            src: _percentiles([r["elapsed"] for r in rows if r.get("source") == src])
            for src in ("fallback", "fast_path")
        },
        fallback_p50_ms=fallback_p50_ms,
        fallback_cost_per_decision=fallback_cost_per_decision,
        policy_volatility=policy_volatility,
        qualification_cost_decisions=qualification_cost_decisions,
        break_even_decisions=break_even_decisions,
        recommendation=recommendation,
        recommendation_reasons=reasons_list,
    )
