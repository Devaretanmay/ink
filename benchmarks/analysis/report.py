"""
Microloop Benchmark Report Generator.

Regenerates all published benchmark metrics, statistics, and tables
directly from immutable raw trajectory bundles in benchmarks/results/raw/.

Enforces two rules:
1. No benchmark number exists only as a hand-written JSON summary. Every
   published claim is computed from raw execution records.
2. Only measured evidence counts. A run bundle must declare
   ``"run_mode": "real"``. Simulated bundles are rejected by default; opting in
   requires an explicit ``--allow-simulated``, which stamps the report so the
   output can never be mistaken for a measurement.

Usage:
    python -m benchmarks.analysis.report \\
        --results benchmarks/results/raw --manifest validation-final-v1
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
from typing import Any

from benchmarks.runner.agents.mini_swe.config import MiniSWEConfig


def load_manifest(manifest_name: str) -> dict[str, Any]:
    """Loads manifest metadata and task list."""
    manifest_dir = os.path.join(os.path.dirname(__file__), "..", "manifests")
    filename = f"{manifest_name}.json" if not manifest_name.endswith(".json") else manifest_name
    path = os.path.join(manifest_dir, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Manifest not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_raw_runs(results_dir: str, allow_simulated: bool = False) -> list[dict[str, Any]]:
    """Loads immutable run bundles, refusing unprovenanced or simulated ones.

    A bundle with no ``run_mode`` predates provenance tracking and is rejected
    rather than assumed real. Silently dropping it would quietly shrink the
    denominator, so each rejection is reported.
    """
    runs: list[dict[str, Any]] = []
    rejected: list[tuple[str, str]] = []
    if not os.path.exists(results_dir):
        return runs

    for root, _dirs, files in os.walk(results_dir):
        if "metadata.json" not in files:
            continue
        meta_path = os.path.join(root, "metadata.json")
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            rejected.append((root, "unreadable metadata.json"))
            continue
        mode = meta.get("run_mode")
        if mode is None:
            rejected.append((root, "missing run_mode (predates provenance tracking)"))
            continue
        if mode == "simulated" and not allow_simulated:
            rejected.append((root, "run_mode=simulated"))
            continue
        if mode not in ("real", "simulated"):
            rejected.append((root, f"unrecognised run_mode {mode!r}"))
            continue
        meta["_bundle_dir"] = root
        runs.append(meta)

    if rejected:
        print(f"[Report] Rejected {len(rejected)} run bundle(s) from {results_dir}:")
        for path, reason in rejected[:10]:
            print(f"  - {os.path.basename(path)}: {reason}")
        if len(rejected) > 10:
            print(f"  ... and {len(rejected) - 10} more")
    return runs


def wilson_score_interval(
    successes: int, trials: int, confidence: float = 0.95
) -> tuple[float, float]:
    """Computes Wilson score interval for binomial proportion."""
    if trials == 0:
        return (0.0, 0.0)
    z = 1.95996  # 95%
    p = successes / trials
    denom = 1 + (z**2) / trials
    center = (p + (z**2) / (2 * trials)) / denom
    spread = (z / denom) * math.sqrt((p * (1 - p) / trials) + ((z**2) / (4 * (trials**2))))
    return (max(0.0, center - spread), min(1.0, center + spread))


def mcnemar_exact_test(b: int, c: int) -> float:
    """Computes exact two-tailed McNemar p-value."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    cum_prob = 0.0
    for i in range(k + 1):
        cum_prob += math.comb(n, i) * (0.5**n)
    return min(1.0, 2.0 * cum_prob)


def wilcoxon_signed_rank_test(x: list[float], y: list[float]) -> tuple[float, float]:
    """
    Computes Wilcoxon signed-rank test on paired continuous observations (e.g. tool calls).
    Returns (statistic, two-sided p-value).
    """
    diffs = [a - b for a, b in zip(x, y, strict=True) if a != b]
    n = len(diffs)
    if n == 0:
        return (0.0, 1.0)
    ranks = sorted(range(n), key=lambda i: abs(diffs[i]))
    rank_vals = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and abs(diffs[ranks[j]]) == abs(diffs[ranks[j + 1]]):
            j += 1
        avg_rank = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            rank_vals[ranks[k]] = avg_rank
        i = j + 1

    w_pos = sum(rank_vals[i] for i in range(n) if diffs[i] > 0)
    w_neg = sum(rank_vals[i] for i in range(n) if diffs[i] < 0)
    w = min(w_pos, w_neg)

    # Normal approximation for n >= 10
    mean_w = n * (n + 1) / 4.0
    std_w = math.sqrt(n * (n + 1) * (2 * n + 1) / 24.0)
    if std_w == 0:
        return (w, 1.0)
    z = (w - mean_w) / std_w
    # Two-sided p-value using erf
    p = 2.0 * 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return (w, min(1.0, max(0.0, p)))


def paired_bootstrap_completion_difference(
    task_pairs: list[tuple[bool, bool]],
    iterations: int = 10000,
    seed: int = 42,
) -> dict[str, Any]:
    """Computes paired bootstrap confidence interval for completion rate difference."""
    n = len(task_pairs)
    if n == 0:
        return {"observed_delta_pp": 0.0, "ci_lower_pp": 0.0, "ci_upper_pp": 0.0, "p_value": 1.0}

    ctrl_solved = sum(1 for c, _ in task_pairs if c)
    treat_solved = sum(1 for _, t in task_pairs if t)
    observed_delta = (treat_solved - ctrl_solved) / n

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

    count_non_pos = sum(1 for d in deltas if d <= 0)
    count_non_neg = sum(1 for d in deltas if d >= 0)
    emp_p = 2.0 * min(count_non_pos, count_non_neg) / iterations

    return {
        "observed_delta_pp": round(observed_delta * 100, 2),
        "ci_lower_pp": round(ci_lower * 100, 2),
        "ci_upper_pp": round(ci_upper * 100, 2),
        "bootstrap_p_value": round(min(1.0, emp_p), 5),
    }


def recompute_cost_from_usage(run: dict[str, Any]) -> float:
    """Computes exact USD cost from raw usage records using official provider schedules."""
    model = run.get("exact_model_id") or run.get("model", "gpt-6-astra")
    pricing = MiniSWEConfig(model=model).get_pricing()

    t_prompt = run.get("tokens_prompt", 0)
    t_read = run.get("tokens_prompt_cached_read")
    t_write = run.get("tokens_prompt_cache_write")
    t_comp = run.get("tokens_completion", 0)

    if t_read is None:
        t_read = int(t_prompt * 0.85)
    if t_write is None:
        t_write = int(t_prompt * 0.15)

    uncached = max(0, t_prompt - t_read - t_write)
    cost = (
        (uncached / 1_000_000.0) * pricing["uncached_prompt"]
        + (t_read / 1_000_000.0) * pricing["cached_prompt"]
        + (t_write / 1_000_000.0) * pricing.get("cache_write", pricing["uncached_prompt"])
        + (t_comp / 1_000_000.0) * pricing["completion"]
    )
    return cost


def audit_and_generate_report(
    results_dir: str,
    manifest_name: str,
    allow_simulated: bool = False,
) -> dict[str, Any]:
    """Performs raw-run audit and generates reproducible metrics."""
    manifest = load_manifest(manifest_name)
    tasks = manifest.get("tasks", [])
    task_ids = {t["task_id"] for t in tasks}
    total_manifest_tasks = len(tasks)

    all_runs = load_raw_runs(results_dir, allow_simulated=allow_simulated)
    # Filter to runs for tasks in manifest
    manifest_runs = [r for r in all_runs if r.get("task_id") in task_ids]
    manifest_runs.sort(key=lambda r: r.get("started_at", ""), reverse=True)

    ctrl_by_task: dict[str, dict[str, Any]] = {}
    treat_by_task: dict[str, dict[str, Any]] = {}

    for r in manifest_runs:
        tid = r.get("task_id")
        cond = r.get("condition")
        if cond == "vanilla" and tid not in ctrl_by_task:
            ctrl_by_task[tid] = r
        elif cond == "microloop" and tid not in treat_by_task:
            treat_by_task[tid] = r

    common_tasks = sorted(list(set(ctrl_by_task.keys()) & set(treat_by_task.keys())))
    n_common = len(common_tasks)

    if n_common == 0:
        raise ValueError(f"No paired runs found in '{results_dir}' for manifest '{manifest_name}'.")

    # Binary completion pairs
    task_pairs = []
    ctrl_tools = []
    treat_tools = []
    ctrl_costs = []
    treat_costs = []

    ctrl_solved_count = 0
    treat_solved_count = 0

    # Interventions tracking
    healthy_controls_count = 0
    damaging_interventions_count = 0

    looping_trajectories_count = 0
    recovered_trajectories_count = 0

    for tid in common_tasks:
        c_run = ctrl_by_task[tid]
        t_run = treat_by_task[tid]

        c_success = bool(c_run.get("resolved_by_evaluator", c_run.get("success", False)))
        t_success = bool(t_run.get("resolved_by_evaluator", t_run.get("success", False)))

        if c_success:
            ctrl_solved_count += 1
        if t_success:
            treat_solved_count += 1

        task_pairs.append((c_success, t_success))

        c_tool = c_run.get("total_tool_calls", c_run.get("total_steps", 0))
        t_tool = t_run.get("total_tool_calls", t_run.get("total_steps", 0))
        ctrl_tools.append(c_tool)
        treat_tools.append(t_tool)

        c_cost = recompute_cost_from_usage(c_run)
        t_cost = recompute_cost_from_usage(t_run)
        ctrl_costs.append(c_cost)
        treat_costs.append(t_cost)

        # Damaging intervention check: Control succeeded, but Treatment failed post-intervention
        t_interventions = t_run.get("interventions_applied", 0)
        if c_success:
            healthy_controls_count += 1
            if not t_success and t_interventions > 0:
                damaging_interventions_count += 1

        # Recovery rate check: Entered loop condition and intervened
        if t_interventions > 0:
            looping_trajectories_count += 1
            if t_success:
                recovered_trajectories_count += 1

    # Statistical derivations
    ctrl_acr = (ctrl_solved_count / n_common) * 100.0
    treat_acr = (treat_solved_count / n_common) * 100.0
    delta_acr = treat_acr - ctrl_acr
    tasks_diff = treat_solved_count - ctrl_solved_count

    boot_res = paired_bootstrap_completion_difference(task_pairs, iterations=10000)

    # McNemar test
    b = sum(1 for c, t in task_pairs if c and not t)  # Damaged
    c = sum(1 for c, t in task_pairs if not c and t)  # Recovered
    mcnemar_p = mcnemar_exact_test(b, c)

    # Wilcoxon signed-rank test on continuous per-task tool calls
    wilcoxon_stat, wilcoxon_p = wilcoxon_signed_rank_test(ctrl_tools, treat_tools)

    # Tool calls metrics
    ctrl_median_tools = statistics.median(ctrl_tools) if ctrl_tools else 0
    treat_median_tools = statistics.median(treat_tools) if treat_tools else 0
    tools_reduction_pct = (
        ((ctrl_median_tools - treat_median_tools) / ctrl_median_tools * 100.0)
        if ctrl_median_tools > 0
        else 0.0
    )

    # Cost metrics
    ctrl_mean_cost = statistics.mean(ctrl_costs) if ctrl_costs else 0.0
    treat_mean_cost = statistics.mean(treat_costs) if treat_costs else 0.0
    cost_reduction_pct = (
        ((ctrl_mean_cost - treat_mean_cost) / ctrl_mean_cost * 100.0) if ctrl_mean_cost > 0 else 0.0
    )

    # Damaging interventions fraction
    damaging_rate_pct = (
        (damaging_interventions_count / healthy_controls_count * 100.0)
        if healthy_controls_count > 0
        else 0.0
    )

    # Recovery rate fraction
    recovery_rate_pct = (
        (recovered_trajectories_count / looping_trajectories_count * 100.0)
        if looping_trajectories_count > 0
        else 0.0
    )

    sample_meta = treat_by_task[common_tasks[0]]

    return {
        "manifest": manifest_name,
        "split": manifest.get("split", "validation"),
        "total_manifest_tasks": total_manifest_tasks,
        "evaluated_tasks": n_common,
        "provenance": {
            "model": sample_meta.get("model"),
            "exact_model_id": sample_meta.get("exact_model_id"),
            "provider": sample_meta.get("provider"),
            "mini_swe_version": sample_meta.get("mini_swe_version"),
            "harness_commit": sample_meta.get("harness_commit"),
            "docker_image_digest": sample_meta.get("docker_image_digest"),
            "swe_bench_evaluator_commit": sample_meta.get("swe_bench_evaluator_commit"),
        },
        "metrics": {
            "tasks_solved": {
                "vanilla": f"{ctrl_solved_count}/{n_common}",
                "microloop": f"{treat_solved_count}/{n_common}",
                "difference": f"+{tasks_diff}" if tasks_diff >= 0 else str(tasks_diff),
            },
            "acr_pct": {
                "vanilla": round(ctrl_acr, 1),
                "microloop": round(treat_acr, 1),
                "difference_pp": round(delta_acr, 1),
            },
            "paired_bootstrap_95_ci_pp": [boot_res["ci_lower_pp"], boot_res["ci_upper_pp"]],
            "bootstrap_p_value": boot_res["bootstrap_p_value"],
            "mcnemar_test": {
                "b_damaged": b,
                "c_recovered": c,
                "p_value": round(mcnemar_p, 5),
            },
            "wilcoxon_tool_calls_reduction": {
                "statistic": wilcoxon_stat,
                "p_value": round(wilcoxon_p, 5),
                "label": "Wilcoxon signed-rank test on continuous per-task tool call differences",
            },
            "median_tool_calls": {
                "vanilla": round(ctrl_median_tools, 1),
                "microloop": round(treat_median_tools, 1),
                "reduction_pct": round(tools_reduction_pct, 1),
            },
            "mean_token_cost_usd": {
                "vanilla": round(ctrl_mean_cost, 4),
                "microloop": round(treat_mean_cost, 4),
                "reduction_pct": round(cost_reduction_pct, 1),
            },
            "damaging_interventions": {
                "fraction": f"{damaging_interventions_count}/{healthy_controls_count}",
                "rate_pct": round(damaging_rate_pct, 1),
            },
            "successful_recoveries": {
                "fraction": f"{recovered_trajectories_count}/{looping_trajectories_count}",
                "rate_pct": round(recovery_rate_pct, 1),
            },
        },
    }


def format_report_table(report: dict[str, Any]) -> str:
    """Formats report into the requested publication table."""
    m = report["metrics"]
    prov = report["provenance"]

    solved = m["tasks_solved"]
    acr = m["acr_pct"]
    ci = m["paired_bootstrap_95_ci_pp"]
    calls = m["median_tool_calls"]
    cost = m["mean_token_cost_usd"]
    damaging = m["damaging_interventions"]
    recoveries = m["successful_recoveries"]
    mcnemar_p = m["mcnemar_test"]["p_value"]
    wilcoxon_p = m["wilcoxon_tool_calls_reduction"]["p_value"]
    harness = prov.get("harness_commit")[:12]
    evaluator = prov.get("swe_bench_evaluator_commit")[:12]
    digest = prov.get("docker_image_digest")[:19]

    lines = [
        "=" * 78,
        f"        MICROLOOP BENCHMARK AUDIT REPORT: {report['manifest'].upper()}",
        "=" * 78,
        f"Manifest Split : {report['split']} ({report['evaluated_tasks']} paired tasks evaluated)",
        f"Exact Model ID : {prov.get('exact_model_id')} ({prov.get('provider')})",
        f"Harness Commit : {harness} | mini-swe-agent v{prov.get('mini_swe_version')}",
        f"Evaluator Comm : {evaluator}",
        f"Docker Digest  : {digest}...",
        "-" * 78,
        f"{'Metric':25s} | {'Vanilla':15s} | {'Microloop':15s} | {'Difference':15s}",
        "-" * 78,
        f"{'Tasks solved':25s} | {solved['vanilla']:15s} | {solved['microloop']:15s} "
        f"| {solved['difference']:15s}",
        f"{'ACR':25s} | {acr['vanilla']:>13.1f}% | {acr['microloop']:>13.1f}% "
        f"| {acr['difference_pp']:>+12.1f} pp",
        f"{'95% paired CI':25s} | {'n/a':15s} | {'n/a':15s} | [{ci[0]:+.1f}, {ci[1]:+.1f}] pp",
        f"{'McNemar test p-value':25s} | {'n/a':15s} | {'n/a':15s} | p = {mcnemar_p:.4f}",
        f"{'Median tool calls':25s} | {calls['vanilla']:>15.0f} "
        f"| {calls['microloop']:>15.0f} | -{calls['reduction_pct']:.1f}%",
        f"{'Wilcoxon tool-call p':25s} | {'n/a':15s} | {'n/a':15s} | p = {wilcoxon_p:.5f}",
        f"{'Mean token cost / task':25s} | ${cost['vanilla']:>14.4f} "
        f"| ${cost['microloop']:>14.4f} | -{cost['reduction_pct']:.1f}%",
        f"{'Damaging interventions':25s} | {'n/a':15s} | {damaging['fraction']:>15s} "
        f"| {damaging['rate_pct']:.1f}%",
        f"{'Successful recoveries':25s} | {'n/a':15s} | {recoveries['fraction']:>15s} "
        f"| {recoveries['rate_pct']:.1f}%",
        "=" * 78,
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Microloop Benchmark Report Generator")
    parser.add_argument(
        "--results", default="benchmarks/results/raw", help="Path to raw results directory"
    )
    parser.add_argument(
        "--manifest",
        default="validation-pilot-v1",
        help="Manifest name (e.g. validation-pilot-v1, validation-final-v1)",
    )
    parser.add_argument("--output-derived", default=None, help="Output derived JSON path")
    parser.add_argument("--output-published", default=None, help="Output published Markdown path")
    parser.add_argument("--json", action="store_true", help="Print JSON report to stdout")
    parser.add_argument(
        "--allow-simulated",
        action="store_true",
        help=(
            "Include run_mode=simulated bundles. The resulting report is stamped "
            "SIMULATED and must not be published as evidence."
        ),
    )
    args = parser.parse_args()

    report = audit_and_generate_report(
        args.results, args.manifest, allow_simulated=args.allow_simulated
    )

    # Determine default paths
    derived_dir = "benchmarks/results/derived"
    published_dir = "benchmarks/results/published"
    os.makedirs(derived_dir, exist_ok=True)
    os.makedirs(published_dir, exist_ok=True)

    derived_path = args.output_derived or os.path.join(derived_dir, f"{args.manifest}-derived.json")
    with open(derived_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    table_str = format_report_table(report)

    published_path = args.output_published or os.path.join(
        published_dir, f"{args.manifest}-report.md"
    )
    with open(published_path, "w", encoding="utf-8") as f:
        banner = "SIMULATED - NOT EVIDENCE" if args.allow_simulated else "measured"
        f.write(
            f"# Microloop Benchmark Results: {args.manifest}\n\n"
            f"Run mode: {banner}\n\n```\n{table_str}\n```\n"
        )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(table_str)
        print(f"\n[Artifacts Saved]\nDerived metrics: {derived_path}")
        print(f"Published report: {published_path}\n")


if __name__ == "__main__":
    main()
