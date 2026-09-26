"""
Statistical Evaluation Module for Microloop Benchmark (Pass 4).

Computes:
1. Primary Analysis: Task-level paired bootstrap confidence interval (B = 10,000)
   on completion rate difference: Delta ACR = ACR_Microloop - ACR_Baseline
2. Secondary Analyses:
   - Wilson score intervals for condition success rates
   - McNemar's test for directly paired task outcomes (p < 0.05)
   - Relative tool-call and token spend reduction on failed/wasteful trajectories
   - Damaging intervention rate (Control succeeded while Treatment failed post-intervention)

Usage:
    python -m benchmarks.analysis.stats --results-dir results_dev \
        --output benchmarks/analysis/dev-results.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import defaultdict
from typing import Any

from benchmarks.runner.agents.mini_swe.config import MiniSWEConfig


def wilson_score_interval(
    successes: int, trials: int, confidence: float = 0.95
) -> tuple[float, float]:
    """Computes Wilson score continuity-adjusted interval for a binomial proportion."""
    if trials == 0:
        return (0.0, 0.0)
    z = 1.95996  # 95% two-sided
    p = successes / trials
    denom = 1 + (z**2) / trials
    center = (p + (z**2) / (2 * trials)) / denom
    spread = (z / denom) * math.sqrt((p * (1 - p) / trials) + ((z**2) / (4 * (trials**2))))
    lower = max(0.0, center - spread)
    upper = min(1.0, center + spread)
    return (round(lower, 4), round(upper, 4))


def mcnemar_exact_test(b: int, c: int) -> float:
    """
    Computes exact two-tailed McNemar p-value using binomial distribution.
    b: Control Success & Treatment Failure
    c: Control Failure & Treatment Success
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    # Binomial CDF under null hypothesis p = 0.5
    cum_prob = 0.0
    for i in range(k + 1):
        cum_prob += math.comb(n, i) * (0.5**n)
    p_val = min(1.0, 2.0 * cum_prob)
    return round(p_val, 6)


def holm_bonferroni_correction(p_values: dict[str, float], alpha: float = 0.05) -> dict[str, Any]:
    """
    Applies Holm-Bonferroni step-down procedure to control Family-Wise Error Rate (FWER)
    across multiple hypotheses in 2026 benchmark evaluations.
    """
    sorted_tests = sorted(p_values.items(), key=lambda x: x[1])
    m = len(sorted_tests)
    adjusted_results = {}
    for rank, (test_name, p_val) in enumerate(sorted_tests, start=1):
        threshold = alpha / (m - rank + 1)
        adjusted_results[test_name] = {
            "unadjusted_p": p_val,
            "threshold": round(threshold, 6),
            "statistically_significant": p_val < threshold,
        }
    return adjusted_results


def paired_bootstrap_delta(
    task_pairs: list[tuple[bool, bool]],
    iterations: int = 10000,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Computes task-level paired bootstrap confidence interval for completion rate difference:
    Delta ACR = Treatment - Control
    """
    n = len(task_pairs)
    if n == 0:
        return {"mean": 0.0, "ci_lower": 0.0, "ci_upper": 0.0, "p_value": 1.0}

    ctrl_successes = sum(1 for c, _ in task_pairs if c)
    treat_successes = sum(1 for _, t in task_pairs if t)
    observed_delta = (treat_successes - ctrl_successes) / n

    rng = random.Random(seed)
    deltas = []
    for _ in range(iterations):
        sample = [task_pairs[rng.randint(0, n - 1)] for _ in range(n)]
        c_acc = sum(1 for c, _ in sample if c) / n
        t_acc = sum(1 for _, t in sample if t) / n
        deltas.append(t_acc - c_acc)

    deltas.sort()
    lower_idx = int(0.025 * iterations)
    upper_idx = int(0.975 * iterations)
    ci_lower = deltas[lower_idx]
    ci_upper = deltas[upper_idx]

    # Empirical two-sided p-value against null (delta = 0)
    count_non_positive = sum(1 for d in deltas if d <= 0)
    count_non_negative = sum(1 for d in deltas if d >= 0)
    emp_p = 2.0 * min(count_non_positive, count_non_negative) / iterations
    emp_p = min(1.0, emp_p)

    return {
        "observed_delta_pp": round(observed_delta * 100, 2),
        "ci_lower_pp": round(ci_lower * 100, 2),
        "ci_upper_pp": round(ci_upper * 100, 2),
        "p_value": round(emp_p, 5),
    }


def load_runs_from_dir(results_dir: str) -> list[dict[str, Any]]:
    """Loads all metadata.json files from results directory."""
    runs = []
    if not os.path.exists(results_dir):
        return runs

    for d in sorted(os.listdir(results_dir)):
        run_path = os.path.join(results_dir, d)
        if os.path.isdir(run_path) and d.startswith("run_"):
            meta_path = os.path.join(run_path, "metadata.json")
            if os.path.exists(meta_path):
                with open(meta_path, encoding="utf-8") as f:
                    try:
                        runs.append(json.load(f))
                    except json.JSONDecodeError:
                        pass
    return runs


def analyze_benchmark_results(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Performs complete scientific evaluation comparing all conditions:
    Vanilla (A), Retry (B), Supervisor (C), Microloop (D).
    """
    conditions_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "trials": 0,
            "successes": 0,
            "total_steps": 0,
            "total_tool_calls": 0,
            "tokens_prompt": 0,
            "tokens_completion": 0,
            "duration_seconds": 0.0,
            "model": "claude-3-7-sonnet-20250219",
            "task_outcomes": {},  # (task_id, seed) -> bool
            "task_steps": {},
            "task_tokens": {},
        }
    )

    for r in runs:
        cond = r.get("condition", "vanilla")
        task_id = r.get("task_id", "unknown")
        seed = r.get("seed", 1)
        success = bool(r.get("success", False))
        steps = r.get("total_steps", 0)
        tool_calls = r.get("total_tool_calls", steps)
        t_prompt = r.get("tokens_prompt", 0)
        t_comp = r.get("tokens_completion", 0)
        dur = r.get("duration_seconds", 0.0)
        model_name = r.get("model", "claude-3-7-sonnet-20250219")

        entry = conditions_data[cond]
        entry["trials"] += 1
        entry["model"] = model_name
        if success:
            entry["successes"] += 1
        entry["total_steps"] += steps
        entry["total_tool_calls"] += tool_calls
        entry["tokens_prompt"] += t_prompt
        entry["tokens_completion"] += t_comp
        entry["duration_seconds"] += dur

        key = (task_id, seed)
        entry["task_outcomes"][key] = success
        entry["task_steps"][key] = steps
        entry["task_tokens"][key] = t_prompt + t_comp

    # Summary table per condition
    summary_per_condition = {}
    for cond, data in conditions_data.items():
        n = data["trials"]
        k = data["successes"]
        acr = k / n if n > 0 else 0.0
        ci_low, ci_high = wilson_score_interval(k, n)
        mean_steps = data["total_steps"] / n if n > 0 else 0.0
        mean_tokens = (data["tokens_prompt"] + data["tokens_completion"]) / n if n > 0 else 0.0

        # Model-Aware Prompt-Cache Economics (85% prompt cache hit rate)
        model_name = data.get("model", "claude-3-7-sonnet-20250219")
        pricing = MiniSWEConfig(model=model_name).get_pricing()
        tot_prompt = data["tokens_prompt"]
        tot_comp = data["tokens_completion"]
        uncached_cost = (tot_prompt * 0.15 / 1_000_000.0) * pricing["uncached_prompt"]
        cached_cost = (tot_prompt * 0.85 / 1_000_000.0) * pricing["cached_prompt"]
        comp_cost = (tot_comp / 1_000_000.0) * pricing["completion"]
        total_cost_usd = uncached_cost + cached_cost + comp_cost
        cost_per_resolved = (total_cost_usd / k) if k > 0 else 0.0

        summary_per_condition[cond] = {
            "trials": n,
            "successes": k,
            "completion_rate_pct": round(acr * 100, 2),
            "wilson_ci_95_pct": [round(ci_low * 100, 2), round(ci_high * 100, 2)],
            "mean_steps_per_task": round(mean_steps, 2),
            "mean_tokens_per_task": int(mean_tokens),
            "total_tokens_spent": tot_prompt + tot_comp,
            "tokens_prompt_uncached": int(tot_prompt * 0.15),
            "tokens_prompt_cached": int(tot_prompt * 0.85),
            "tokens_completion": tot_comp,
            "estimated_cost_usd": round(total_cost_usd, 4),
            "cost_per_resolved_task_usd": round(cost_per_resolved, 4),
        }

    # Paired comparisons against Control (Vanilla)
    paired_analyses = {}
    ctrl = conditions_data.get("vanilla")

    for treatment_name in ("microloop", "retry", "supervisor"):
        treat = conditions_data.get(treatment_name)
        if not ctrl or not treat:
            continue

        # Common keys across (task_id, seed)
        common_keys = sorted(
            list(set(ctrl["task_outcomes"].keys()) & set(treat["task_outcomes"].keys()))
        )
        if not common_keys:
            continue

        pairs = [(ctrl["task_outcomes"][k], treat["task_outcomes"][k]) for k in common_keys]
        bootstrap_res = paired_bootstrap_delta(pairs, iterations=10000)

        # McNemar contingency
        # b: ctrl=1, treat=0 (Damaged)
        # c: ctrl=0, treat=1 (Recovered)
        b = sum(1 for c_res, t_res in pairs if c_res and not t_res)
        c = sum(1 for c_res, t_res in pairs if not c_res and t_res)
        mcnemar_p = mcnemar_exact_test(b, c)

        # Damaging Intervention Rate
        ctrl_success_count = sum(1 for c_res, _ in pairs if c_res)
        damaging_rate = (b / ctrl_success_count * 100.0) if ctrl_success_count > 0 else 0.0

        # Wasted steps & token savings on tasks where Control failed
        failed_keys = [k for k in common_keys if not ctrl["task_outcomes"][k]]
        ctrl_failed_steps = sum(ctrl["task_steps"].get(k, 0) for k in failed_keys)
        treat_failed_steps = sum(treat["task_steps"].get(k, 0) for k in failed_keys)
        step_reduction_pct = (
            ((ctrl_failed_steps - treat_failed_steps) / ctrl_failed_steps * 100.0)
            if ctrl_failed_steps > 0
            else 0.0
        )

        ctrl_failed_tok = sum(ctrl["task_tokens"].get(k, 0) for k in failed_keys)
        treat_failed_tok = sum(treat["task_tokens"].get(k, 0) for k in failed_keys)
        token_reduction_pct = (
            ((ctrl_failed_tok - treat_failed_tok) / ctrl_failed_tok * 100.0)
            if ctrl_failed_tok > 0
            else 0.0
        )

        paired_analyses[f"{treatment_name}_vs_vanilla"] = {
            "paired_tasks_count": len(common_keys),
            "paired_bootstrap": bootstrap_res,
            "mcnemar_test": {
                "b_control_success_treatment_fail": b,
                "c_control_fail_treatment_success": c,
                "p_value": mcnemar_p,
                "statistically_significant": mcnemar_p < 0.05,
            },
            "damaging_intervention_rate_pct": round(damaging_rate, 2),
            "wasted_steps_reduction_on_failures_pct": round(step_reduction_pct, 2),
            "wasted_tokens_reduction_on_failures_pct": round(token_reduction_pct, 2),
        }

    # Multiple testing correction (Holm-Bonferroni FWER)
    p_values_for_correction = {
        name: data["mcnemar_test"]["p_value"] for name, data in paired_analyses.items()
    }
    fwer_results = holm_bonferroni_correction(p_values_for_correction, alpha=0.05)
    for name, fwer_info in fwer_results.items():
        if name in paired_analyses:
            paired_analyses[name]["fwer_correction"] = fwer_info

    return {
        "conditions": summary_per_condition,
        "primary_analysis": paired_analyses.get("microloop_vs_vanilla", {}),
        "secondary_analyses": paired_analyses,
        "fwer_multiple_testing": fwer_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Microloop Scientific Statistical Evaluation")
    parser.add_argument("--results-dir", default="results", help="Directory containing run results")
    parser.add_argument(
        "--output",
        default="benchmarks/analysis/dev-results.json",
        help="Output path for results JSON",
    )
    parser.add_argument("--json", action="store_true", help="Print json output only")
    args = parser.parse_args()

    runs = load_runs_from_dir(args.results_dir)
    if not runs:
        print(f"[Error] No runs found in directory: {args.results_dir}", file=sys.stderr)
        sys.exit(1)

    analysis = analyze_benchmark_results(runs)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2)

    if args.json:
        print(json.dumps(analysis, indent=2))
        return

    # Print formatted statistical report
    print("\n" + "=" * 80)
    print("      MICROLOOP VALIDATION BENCHMARK: STATISTICAL REPORT")
    print("=" * 80)
    print(f"Total Trajectory Runs Analyzed: {len(runs)}")
    print("-" * 80)
    print(
        f"{'Condition':12s} | {'Trials':6s} | {'Success':7s} | {'ACR':7s} | "
        f"{'95% Wilson CI':15s} | {'Steps':6s} | {'$/Resolve':9s}"
    )
    print("-" * 80)

    for cond, dat in analysis.get("conditions", {}).items():
        ci = dat["wilson_ci_95_pct"]
        cost_str = (
            f"${dat['cost_per_resolved_task_usd']:.3f}"
            if dat["cost_per_resolved_task_usd"] > 0
            else "N/A"
        )
        print(
            f"{cond:12s} | {dat['trials']:6d} | {dat['successes']:7d} | "
            f"{dat['completion_rate_pct']:6.2f}% | "
            f"[{ci[0]:5.1f}%, {ci[1]:5.1f}%] | "
            f"{dat['mean_steps_per_task']:6.1f} | {cost_str:>9s}"
        )
    print("-" * 80)

    primary = analysis.get("primary_analysis", {})
    if primary:
        boot = primary.get("paired_bootstrap", {})
        mcnemar = primary.get("mcnemar_test", {})
        fwer = primary.get("fwer_correction", {})
        delta = boot.get("observed_delta_pp", 0.0)
        ci_lo = boot.get("ci_lower_pp", 0.0)
        ci_hi = boot.get("ci_upper_pp", 0.0)
        b_count = mcnemar.get("b_control_success_treatment_fail")
        c_count = mcnemar.get("c_control_fail_treatment_success")
        mcnemar_p = mcnemar.get("p_value")
        alpha_adj = fwer.get("threshold", 0.05)
        significant = fwer.get("statistically_significant", False)
        damaging = primary.get("damaging_intervention_rate_pct", 0.0)
        step_saved = primary.get("wasted_steps_reduction_on_failures_pct", 0.0)
        token_saved = primary.get("wasted_tokens_reduction_on_failures_pct", 0.0)

        print("PRIMARY HYPOTHESIS H1: Microloop vs. Vanilla Baseline")
        print(f"  • Paired Tasks Evaluated       : {primary.get('paired_tasks_count')}")
        print(f"  • Delta ACR (Treatment Lift)   : +{delta:.2f} percentage points")
        print(f"  • 95% Bootstrap CI (B=10,000)  : [{ci_lo:+.2f} pp, {ci_hi:+.2f} pp]")
        print(f"  • Bootstrap p-value            : p = {boot.get('p_value'):.5f}")
        print(f"  • McNemar Paired Test          : b={b_count}, c={c_count} (p = {mcnemar_p:.5f})")
        print(f"  • Holm-Bonferroni FWER         : alpha_adj = {alpha_adj}, sig = {significant}")
        print(f"  • Damaging Intervention Rate   : {damaging:.2f}% (Target: < 5.0%)")
        print(f"  • Wasted Steps Reduction       : {step_saved:.2f}% (Target: >= 15.0%)")
        print(f"  • Wasted Tokens Reduction      : {token_saved:.2f}%")

    fwer_all = analysis.get("fwer_multiple_testing", {})
    if fwer_all:
        print("\nHOLM-BONFERRONI FAMILY-WISE ERROR RATE (FWER) CORRECTION (alpha = 0.05):")
        for test_name, res in fwer_all.items():
            sig_mark = (
                "PASS (Significant)"
                if res.get("statistically_significant")
                else "FAIL (Not Significant)"
            )
            unadjusted = res.get("unadjusted_p")
            threshold = res.get("threshold")
            print(
                f"  • {test_name:24s}: unadj_p = {unadjusted:.6f} "
                f"vs threshold {threshold:.6f} -> {sig_mark}"
            )
    print("=" * 80)
    if args.output:
        print(f"Full report saved to: {args.output}\n")


if __name__ == "__main__":
    main()
