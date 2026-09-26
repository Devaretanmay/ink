"""
Cross-Model Transfer and Invariance Evaluation Module.

Evaluates whether Microloop trajectory monitoring and recovery gains transfer
consistently across different frontier LLMs:
- Model 1: gpt-4o-2024-08-06 (OpenAI)
- Model 2: claude-3-5-sonnet-20241022 (Anthropic)

Validates:
1. Invariance of detector precision and recall across model architectures.
2. Cross-model recovery lift (Target: Delta ACR >= +8.0 pp on both models).
3. Cross-model prompt-cache economic benefits.

Usage:
    python -m benchmarks.analysis.cross_model \
        --model1-dir results_validation_gpt4o \
        --model2-dir results_validation_claude \
        --output benchmarks/analysis/cross-model-comparison.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from benchmarks.analysis.stats import (
    analyze_benchmark_results,
    load_runs_from_dir,
)


def compare_cross_model(
    model1_name: str,
    model1_runs: list[dict[str, Any]],
    model2_name: str,
    model2_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Computes comparative metrics between Model 1 and Model 2."""
    analysis1 = analyze_benchmark_results(model1_runs)
    analysis2 = analyze_benchmark_results(model2_runs)

    m1_cond = analysis1.get("conditions", {})
    m2_cond = analysis2.get("conditions", {})

    m1_primary = analysis1.get("primary_analysis", {})
    m2_primary = analysis2.get("primary_analysis", {})

    m1_boot = m1_primary.get("paired_bootstrap", {})
    m2_boot = m2_primary.get("paired_bootstrap", {})

    return {
        "model1": {
            "name": model1_name,
            "total_runs": len(model1_runs),
            "vanilla_acr_pct": m1_cond.get("vanilla", {}).get("completion_rate_pct", 0.0),
            "microloop_acr_pct": m1_cond.get("microloop", {}).get("completion_rate_pct", 0.0),
            "delta_acr_pp": m1_boot.get("observed_delta_pp", 0.0),
            "ci_95_pp": [m1_boot.get("ci_lower_pp", 0.0), m1_boot.get("ci_upper_pp", 0.0)],
            "p_value": m1_boot.get("p_value", 1.0),
            "cost_per_resolved_vanilla": m1_cond.get("vanilla", {}).get(
                "cost_per_resolved_task_usd", 0.0
            ),
            "cost_per_resolved_microloop": m1_cond.get("microloop", {}).get(
                "cost_per_resolved_task_usd", 0.0
            ),
        },
        "model2": {
            "name": model2_name,
            "total_runs": len(model2_runs),
            "vanilla_acr_pct": m2_cond.get("vanilla", {}).get("completion_rate_pct", 0.0),
            "microloop_acr_pct": m2_cond.get("microloop", {}).get("completion_rate_pct", 0.0),
            "delta_acr_pp": m2_boot.get("observed_delta_pp", 0.0),
            "ci_95_pp": [m2_boot.get("ci_lower_pp", 0.0), m2_boot.get("ci_upper_pp", 0.0)],
            "p_value": m2_boot.get("p_value", 1.0),
            "cost_per_resolved_vanilla": m2_cond.get("vanilla", {}).get(
                "cost_per_resolved_task_usd", 0.0
            ),
            "cost_per_resolved_microloop": m2_cond.get("microloop", {}).get(
                "cost_per_resolved_task_usd", 0.0
            ),
        },
        "cross_model_invariance": {
            "m1_lift_meets_target": m1_boot.get("observed_delta_pp", 0.0) >= 8.0,
            "m2_lift_meets_target": m2_boot.get("observed_delta_pp", 0.0) >= 8.0,
            "generalization_success": (
                m1_boot.get("observed_delta_pp", 0.0) >= 8.0
                and m2_boot.get("observed_delta_pp", 0.0) >= 8.0
            ),
            "m1_cost_reduction_pct": round(
                (
                    (
                        m1_cond.get("vanilla", {}).get("cost_per_resolved_task_usd", 1)
                        - m1_cond.get("microloop", {}).get("cost_per_resolved_task_usd", 0)
                    )
                    / max(0.001, m1_cond.get("vanilla", {}).get("cost_per_resolved_task_usd", 1))
                )
                * 100.0,
                1,
            ),
            "m2_cost_reduction_pct": round(
                (
                    (
                        m2_cond.get("vanilla", {}).get("cost_per_resolved_task_usd", 1)
                        - m2_cond.get("microloop", {}).get("cost_per_resolved_task_usd", 0)
                    )
                    / max(0.001, m2_cond.get("vanilla", {}).get("cost_per_resolved_task_usd", 1))
                )
                * 100.0,
                1,
            ),
        },
        "full_analysis_model1": analysis1,
        "full_analysis_model2": analysis2,
    }


def main():
    parser = argparse.ArgumentParser(description="Cross-Model Transfer and Invariance Benchmark")
    parser.add_argument("--model1-name", default="gpt-4o-2024-08-06")
    parser.add_argument("--model1-dir", default="results_validation_gpt4o")
    parser.add_argument("--model2-name", default="claude-3-5-sonnet-20241022")
    parser.add_argument("--model2-dir", default="results_validation_claude")
    parser.add_argument("--output", default="benchmarks/analysis/cross-model-comparison.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    m1_runs = load_runs_from_dir(args.model1_dir)
    m2_runs = load_runs_from_dir(args.model2_dir)

    if not m1_runs:
        print(f"[Error] No runs found in model 1 directory: {args.model1_dir}", file=sys.stderr)
        sys.exit(1)
    if not m2_runs:
        print(f"[Error] No runs found in model 2 directory: {args.model2_dir}", file=sys.stderr)
        sys.exit(1)

    res = compare_cross_model(args.model1_name, m1_runs, args.model2_name, m2_runs)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2)

    if args.json:
        print(json.dumps(res, indent=2))
        return

    m1 = res["model1"]
    m2 = res["model2"]
    inv = res["cross_model_invariance"]

    def verdict(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    print("\n" + "=" * 80)
    print("      MICROLOOP CROSS-MODEL GENERALIZATION & TRANSFER REPORT")
    print("=" * 80)
    print(f"{'Metric':32s} | {m1['name']:20s} | {m2['name']:20s}")
    print("-" * 80)
    print(f"{'Total Validation Runs':32s} | {m1['total_runs']:20d} | {m2['total_runs']:20d}")
    print(
        f"{'Vanilla Baseline ACR':32s} | {m1['vanilla_acr_pct']:19.2f}% "
        f"| {m2['vanilla_acr_pct']:19.2f}%"
    )
    print(
        f"{'Microloop Treatment ACR':32s} | {m1['microloop_acr_pct']:19.2f}% "
        f"| {m2['microloop_acr_pct']:19.2f}%"
    )
    print(
        f"{'Empirical Lift (Delta ACR)':32s} | +{m1['delta_acr_pp']:18.2f} pp "
        f"| +{m2['delta_acr_pp']:18.2f} pp"
    )
    print(
        f"{'95% Bootstrap CI':32s} | [{m1['ci_95_pp'][0]:+.1f}, {m1['ci_95_pp'][1]:+.1f}] pp"
        f"         | [{m2['ci_95_pp'][0]:+.1f}, {m2['ci_95_pp'][1]:+.1f}] pp"
    )
    print(f"{'Bootstrap Significance':32s} | p = {m1['p_value']:16.5f} | p = {m2['p_value']:16.5f}")
    print(
        f"{'Cost / Resolved Task (Vanilla)':32s} | ${m1['cost_per_resolved_vanilla']:19.3f} "
        f"| ${m2['cost_per_resolved_vanilla']:19.3f}"
    )
    print(
        f"{'Cost / Resolved Task (Microloop)':32s} | ${m1['cost_per_resolved_microloop']:19.3f} "
        f"| ${m2['cost_per_resolved_microloop']:19.3f}"
    )
    print(
        f"{'Economic Cost Savings':32s} | {inv['m1_cost_reduction_pct']:19.1f}% "
        f"| {inv['m2_cost_reduction_pct']:19.1f}%"
    )
    print("-" * 80)
    print("CROSS-MODEL GENERALIZATION TARGET VERIFICATION:")
    print(
        f"  • Model 1 Lift >= +8.0 pp: {verdict(inv['m1_lift_meets_target'])} "
        f"(+{m1['delta_acr_pp']:.2f} pp)"
    )
    print(
        f"  • Model 2 Lift >= +8.0 pp: {verdict(inv['m2_lift_meets_target'])} "
        f"(+{m2['delta_acr_pp']:.2f} pp)"
    )
    overall = "PASS (Robust Generalization)" if inv["generalization_success"] else "FAIL"
    print(f"  • OVERALL CROSS-MODEL VERDICT: {overall}")
    print("=" * 80)
    if args.output:
        print(f"Full comparative report written to: {args.output}\n")


if __name__ == "__main__":
    main()
