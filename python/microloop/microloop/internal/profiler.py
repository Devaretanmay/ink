"""Site profiler: trustworthy summaries from local decision history."""

from __future__ import annotations

from collections import Counter


def _percentiles(values, points=(50, 95)):
    if not values:
        return {f"p{p}": None for p in points}
    ordered = sorted(values)
    out = {}
    for p in points:
        rank = min(len(ordered) - 1, int(len(ordered) * p / 100))
        out[f"p{p}"] = ordered[rank]
    return out


def profile_history(rows):
    """Summarize frequency, repetition, choices, outcomes, usage.

    Rows are decoded history records from DecisionStore.history().
    Missing outcomes stay distinct from unsuccessful outcomes.
    """
    observations = len(rows)
    tasks = [r.get("task") for r in rows]
    states = [r.get("state") for r in rows]
    # States arrive decoded as dicts; count repetition by canonical form.
    import json

    def key(s):
        return json.dumps(s, sort_keys=True, separators=(",", ":")) if s is not None else ""

    state_counts = Counter(key(s) for s in states)
    choices = Counter(r.get("choice") for r in rows)
    sources = Counter(r.get("source") for r in rows)
    reasons = Counter(r.get("reason") for r in rows if r.get("reason"))

    missing = sum(1 for r in rows if r.get("outcome") is None)
    with_outcome = [r for r in rows if r.get("outcome") is not None]
    successful = sum(1 for r in with_outcome if r["outcome"].get("quality") == 1)
    unsuccessful = len(with_outcome) - successful

    usage = {}
    for field in ("model_calls", "input_tokens", "output_tokens", "cost"):
        known = [
            r["usage"][field]
            for r in rows
            if isinstance(r.get("usage"), dict) and r["usage"].get(field) is not None
        ]
        usage[field] = sum(known) if known else None
    usage["rows_with_usage"] = sum(
        1
        for r in rows
        if isinstance(r.get("usage"), dict) and any(v is not None for v in r["usage"].values())
    )

    elapsed = [r["elapsed"] for r in rows if isinstance(r.get("elapsed"), (int, float))]
    lat = _percentiles(elapsed)

    distinct_tasks = len(set(tasks)) if rows else 0
    return {
        "observations": observations,
        "distinct_tasks": distinct_tasks,
        "single_use_tasks": distinct_tasks == observations if rows else True,
        "unique_states": len(state_counts),
        "repetition_rate": 1 - len(state_counts) / observations if observations else 0,
        "most_common_states": state_counts.most_common(5),
        "choices": dict(choices),
        "sources": dict(sources),
        "fallback_reasons": dict(reasons),
        "outcome_completeness": len(with_outcome) / observations if observations else 0,
        "missing_outcomes": missing,
        "successful_outcomes": successful,
        "unsuccessful_outcomes": unsuccessful,
        "usage": usage,
        "elapsed_p50": lat["p50"],
        "elapsed_p95": lat["p95"],
    }
