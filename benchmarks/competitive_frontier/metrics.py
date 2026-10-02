"""Statistical, economic, and operational metrics for the competitive frontier benchmark."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def wilson_score_interval(
    k: int, n: int, confidence: float = 0.95
) -> tuple[float, float, float]:
    """Calculates the Wilson score binomial confidence interval.

    Returns (lower_bound, upper_bound, center).
    If n == 0, returns (0.0, 1.0, 0.0).
    """
    if n <= 0:
        return 0.0, 1.0, 0.0
    k = max(0, min(k, n))
    # z = 1.95996 for 95% confidence
    z = 1.959963984540054 if confidence == 0.95 else 2.5758293035489004
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    spread = (z / denom) * math.sqrt((p * (1.0 - p) / n) + (z2 / (4.0 * n * n)))
    lower = max(0.0, center - spread)
    upper = min(1.0, center + spread)
    return lower, upper, center


def rule_of_three_upper_bound(n: int) -> float:
    """Calculates approximate 95% upper bound when zero events are observed: ~ 3 / n."""
    if n <= 0:
        return 1.0
    return min(1.0, 3.0 / n)


def calculate_percentiles(values: list[float]) -> dict[str, float]:
    """Returns p50, p95, and p99 percentiles in milliseconds."""
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    arr = np.array(values, dtype=np.float64)
    return {
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
    }


def compute_cost_weighted_error(
    decisions: list[dict[str, Any]], severity_matrix: dict[str, float]
) -> float:
    """Computes total cost-weighted error across served decisions."""
    total_penalty = 0.0
    for d in decisions:
        if d.get("served_by") in ("cache", "local", "fast_path", "classifier") and not d.get(
            "verified_outcome_correct", True
        ):
            chosen = d.get("decision", "")
            penalty = severity_matrix.get(chosen, severity_matrix.get("default", 1.0))
            total_penalty += penalty
    return total_penalty


def compute_economic_summary(
    eval_decisions: list[dict[str, Any]],
    baseline_eval_decisions: list[dict[str, Any]],
    decision_site_share: float,
    qualification_cost: float = 0.0,
    training_cost: float = 0.0,
    retraining_cost: float = 0.0,
    comparison_cost: float = 0.0,
) -> dict[str, Any]:
    """Computes DecisionSite vs Whole-application savings and economics."""
    total_eval = len(eval_decisions)
    if total_eval == 0:
        return {}

    orig_calls_avoided = sum(
        1 for d in eval_decisions if not d.get("original_model_called", True)
    )
    eligible_site_call_reduction = (
        (orig_calls_avoided / total_eval) * 100.0 if total_eval > 0 else 0.0
    )
    whole_app_call_reduction = eligible_site_call_reduction * decision_site_share

    baseline_spend = sum(d.get("cost_usd", 0.0) for d in baseline_eval_decisions)
    direct_spend = sum(d.get("cost_usd", 0.0) for d in eval_decisions)
    overhead_cost = (
        qualification_cost + training_cost + retraining_cost + comparison_cost
    )
    net_spend = direct_spend + overhead_cost

    eligible_site_cost_reduction = (
        ((baseline_spend - net_spend) / baseline_spend) * 100.0
        if baseline_spend > 0
        else 0.0
    )
    whole_app_spend_reduction = eligible_site_cost_reduction * decision_site_share

    return {
        "total_eval_decisions": total_eval,
        "original_model_calls": sum(
            1 for d in eval_decisions if d.get("original_model_called", False)
        ),
        "cheap_model_calls": sum(
            1 for d in eval_decisions if d.get("cheap_model_called", False)
        ),
        "local_serves": sum(
            1
            for d in eval_decisions
            if d.get("served_by") in ("cache", "local", "fast_path", "classifier")
        ),
        "original_calls_avoided": orig_calls_avoided,
        "eligible_site_call_reduction_pct": round(eligible_site_call_reduction, 2),
        "whole_app_call_reduction_pct": round(whole_app_call_reduction, 2),
        "baseline_spend_usd": round(baseline_spend, 6),
        "direct_spend_usd": round(direct_spend, 6),
        "overhead_cost_usd": round(overhead_cost, 6),
        "net_spend_usd": round(net_spend, 6),
        "net_savings_usd": round(baseline_spend - net_spend, 6),
        "eligible_site_cost_reduction_pct": round(eligible_site_cost_reduction, 2),
        "whole_app_spend_reduction_pct": round(whole_app_spend_reduction, 2),
        "decision_site_share": decision_site_share,
    }
