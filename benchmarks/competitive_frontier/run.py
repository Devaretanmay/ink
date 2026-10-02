"""Command-line harness to run the Competitive Safety × Savings Frontier Benchmark.

Usage:
    python benchmarks/competitive_frontier/run.py --workload all --arms all --mode both
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath("python/microloop"))

from benchmarks.competitive_frontier.arms import ArmRunner
from benchmarks.competitive_frontier.metrics import (
    calculate_percentiles,
    compute_cost_weighted_error,
    compute_economic_summary,
    rule_of_three_upper_bound,
    wilson_score_interval,
)
from benchmarks.competitive_frontier.workloads import (
    WORKLOAD_CONFIGS,
    WorkloadConfig,
    get_workload_dataset,
)


def evaluate_run_metrics(
    eval_decisions: list[dict[str, Any]],
    baseline_decisions: list[dict[str, Any]],
    workload_cfg: WorkloadConfig,
    arm_name: str,
    config_dict: dict[str, Any],
    mode: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = meta or {}
    total = len(eval_decisions)
    if total == 0:
        return {}

    local_serves = [
        d for d in eval_decisions if d.get("served_by") in ("cache", "local", "fast_path", "classifier")
    ]
    n_local = len(local_serves)
    wrong_serves = [d for d in local_serves if not d.get("verified_outcome_correct", True)]
    k_wrong = len(wrong_serves)

    w_lower, w_upper, w_center = wilson_score_interval(k_wrong, n_local, confidence=0.95)
    r3_upper = rule_of_three_upper_bound(n_local) if k_wrong == 0 else w_upper
    wrong_serve_rate = (k_wrong / n_local * 100.0) if n_local > 0 else 0.0

    # Model incorrect decisions (when served by model)
    model_serves = [d for d in eval_decisions if d.get("served_by") in ("teacher", "cheap_model")]
    incorrect_model_serves = sum(1 for d in model_serves if not d.get("verified_outcome_correct", True))
    model_error_rate = (
        (incorrect_model_serves / len(model_serves) * 100.0) if model_serves else 0.0
    )

    # Cost-weighted error
    weighted_error = compute_cost_weighted_error(eval_decisions, workload_cfg.severity_matrix)

    # Agreement with teacher
    agreed_teacher = sum(1 for d in eval_decisions if d.get("decision") == d.get("teacher_decision"))
    teacher_agreement_pct = (agreed_teacher / total) * 100.0

    # Overall outcome correctness across all served decisions
    outcome_correct_total = sum(1 for d in eval_decisions if d.get("verified_outcome_correct", True))
    outcome_correctness_pct = (outcome_correct_total / total) * 100.0

    # Economic summary
    econ = compute_economic_summary(
        eval_decisions,
        baseline_decisions,
        decision_site_share=workload_cfg.decision_site_share,
        qualification_cost=meta.get("qualification_cost", 0.0),
        training_cost=meta.get("training_cost", 0.0),
        retraining_cost=meta.get("retraining_cost", 0.0),
        comparison_cost=meta.get("comparison_cost", 0.0),
    )

    # Latencies
    local_lats = [d["latency_ms"] for d in local_serves]
    fallback_lats = [
        d["latency_ms"]
        for d in eval_decisions
        if d.get("served_by") in ("fallback", "teacher")
    ]
    e2e_lats = [d["latency_ms"] for d in eval_decisions]

    # Workflow latency: each workflow has (workflow_calls_total - 1) other calls × baseline latency
    other_calls_count = eval_decisions[0].get("workflow_calls_total", 5) - 1
    other_calls_latency = other_calls_count * workload_cfg.teacher_latency_base_ms
    workflow_lats = [d["latency_ms"] + other_calls_latency for d in eval_decisions]

    p_local = calculate_percentiles(local_lats)
    p_fallback = calculate_percentiles(fallback_lats)
    p_e2e = calculate_percentiles(e2e_lats)
    p_workflow = calculate_percentiles(workflow_lats)

    # Cold start metrics
    first_saving_call = None
    accumulated_net = - (meta.get("qualification_cost", 0.0) + meta.get("training_cost", 0.0))
    breakeven_call = None
    base_cost_per_call = baseline_decisions[0]["cost_usd"] if baseline_decisions else 0.0005

    for idx, d in enumerate(eval_decisions):
        if not d.get("original_model_called", True):
            if first_saving_call is None:
                first_saving_call = idx + 1
            saving = base_cost_per_call - d.get("cost_usd", 0.0)
            accumulated_net += saving
            if breakeven_call is None and accumulated_net > 0:
                breakeven_call = idx + 1

    # Drift metrics
    drift_items = [d for d in eval_decisions if d.get("phase") in ("eval_drift", "eval_post_drift")]
    drift_local = [d for d in drift_items if d.get("served_by") in ("cache", "local", "fast_path", "classifier")]
    drift_wrong = sum(1 for d in drift_local if not d.get("verified_outcome_correct", True))

    return {
        "workload": workload_cfg.name,
        "arm": arm_name,
        "mode": mode,
        "configuration": config_dict,
        "total_decisions": total,
        "local_serves": n_local,
        "wrong_serves": k_wrong,
        "wrong_serve_rate_pct": round(wrong_serve_rate, 4),
        "wilson_ci_lower_pct": round(w_lower * 100.0, 4),
        "wilson_ci_upper_pct": round(w_upper * 100.0, 4),
        "rule_of_three_upper_pct": round(r3_upper * 100.0, 4),
        "model_error_rate_pct": round(model_error_rate, 4),
        "cost_weighted_error": round(weighted_error, 2),
        "teacher_agreement_pct": round(teacher_agreement_pct, 2),
        "outcome_correctness_pct": round(outcome_correctness_pct, 2),
        "eligible_site_call_reduction_pct": econ.get("eligible_site_call_reduction_pct", 0.0),
        "whole_app_call_reduction_pct": econ.get("whole_app_call_reduction_pct", 0.0),
        "eligible_site_cost_reduction_pct": econ.get("eligible_site_cost_reduction_pct", 0.0),
        "whole_app_spend_reduction_pct": econ.get("whole_app_spend_reduction_pct", 0.0),
        "net_savings_usd": econ.get("net_savings_usd", 0.0),
        "net_spend_usd": econ.get("net_spend_usd", 0.0),
        "baseline_spend_usd": econ.get("baseline_spend_usd", 0.0),
        "overhead_cost_usd": econ.get("overhead_cost_usd", 0.0),
        "latency_local_p50": round(p_local["p50"], 2),
        "latency_local_p95": round(p_local["p95"], 2),
        "latency_local_p99": round(p_local["p99"], 2),
        "latency_fallback_p50": round(p_fallback["p50"], 2),
        "latency_fallback_p95": round(p_fallback["p95"], 2),
        "latency_e2e_p50": round(p_e2e["p50"], 2),
        "latency_e2e_p95": round(p_e2e["p95"], 2),
        "latency_e2e_p99": round(p_e2e["p99"], 2),
        "latency_workflow_p50": round(p_workflow["p50"], 2),
        "latency_workflow_p95": round(p_workflow["p95"], 2),
        "latency_workflow_p99": round(p_workflow["p99"], 2),
        "cold_start_calls_to_first_saving": first_saving_call,
        "cold_start_calls_to_breakeven": breakeven_call,
        "drift_wrong_serves": drift_wrong,
        "wrong_serves_before_revocation": meta.get("wrong_serves_before_demotion", drift_wrong if n_local > 0 else 0),
        "revocations": meta.get("demotions", 0),
        "false_revocations": 0,
        "requalifications": meta.get("requalifications", 0),
    }


def main():
    parser = argparse.ArgumentParser(description="Run Competitive Safety x Savings Frontier Benchmark")
    parser.add_argument("--workload", default="all", choices=["all", "support", "tool_select", "incident_triage", "research_novelty"])
    parser.add_argument("--arms", default="all", help="Comma-separated list or 'all'")
    parser.add_argument("--mode", default="both", choices=["passive_drift", "explicit_change", "both"])
    parser.add_argument("--output", default="benchmarks/results/competitive_frontier")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)
    decisions_log_path = os.path.join(args.output, "decisions.jsonl")
    summary_path = os.path.join(args.output, "summary.json")

    workload_names = list(WORKLOAD_CONFIGS.keys()) if args.workload == "all" else [args.workload]
    modes = ["passive_drift", "explicit_change"] if args.mode == "both" else [args.mode]

    all_summaries: list[dict[str, Any]] = []

    # Open decisions log file in append/write mode
    with open(decisions_log_path, "w") as dec_file:
        for w_name in workload_names:
            print(f"\n=======================================================")
            print(f"Executing Workload: {w_name.upper()}")
            print(f"=======================================================")
            cfg, dataset = get_workload_dataset(w_name)
            runner = ArmRunner(cfg, dataset)

            # Establish Baseline: Arm A (Teacher Model)
            print("Running Arm A: Original Teacher Model...")
            arm_a_decisions = runner.run_arm_a()
            for d in arm_a_decisions:
                dec_file.write(json.dumps(d) + "\n")

            metrics_a = evaluate_run_metrics(
                arm_a_decisions,
                arm_a_decisions,
                cfg,
                arm_name="original_model",
                config_dict={"model": "teacher"},
                mode="baseline",
            )
            all_summaries.append(metrics_a)

            for mode in modes:
                flush = (mode == "explicit_change")
                print(f"\n--- Running Mode: {mode.upper()} ---")

                # Arm B: Exact Cache Sweeps
                exact_configs = [
                    {"min_observations": 1, "ttl": None},
                    {"min_observations": 2, "ttl": None},
                    {"min_observations": 3, "ttl": None},
                    {"min_observations": 5, "ttl": None},
                    {"min_observations": 2, "ttl": 400},
                ]
                for ec in exact_configs:
                    decisions = runner.run_arm_b(
                        min_observations=ec["min_observations"],
                        ttl=ec["ttl"],
                        flush_on_drift=flush,
                    )
                    for d in decisions:
                        dec_file.write(json.dumps(d) + "\n")
                    m = evaluate_run_metrics(
                        decisions,
                        arm_a_decisions,
                        cfg,
                        arm_name="exact_cache",
                        config_dict=ec,
                        mode=mode,
                    )
                    all_summaries.append(m)

                # Arm C: Semantic Cache Sweeps
                sem_thresholds = [0.70, 0.75, 0.80, 0.85, 0.90, 0.925, 0.95, 0.975, 0.99]
                for st in sem_thresholds:
                    decisions = runner.run_arm_c(
                        similarity_threshold=st,
                        flush_on_drift=flush,
                    )
                    for d in decisions:
                        dec_file.write(json.dumps(d) + "\n")
                    m = evaluate_run_metrics(
                        decisions,
                        arm_a_decisions,
                        cfg,
                        arm_name="semantic_cache",
                        config_dict={"similarity_threshold": st},
                        mode=mode,
                    )
                    all_summaries.append(m)

                # Arm D: Cheaper Model Sweeps
                cheap_confs = [0.0, 0.60, 0.70, 0.80, 0.85, 0.90, 0.95]
                for cc in cheap_confs:
                    decisions = runner.run_arm_d(confidence_threshold=cc)
                    for d in decisions:
                        dec_file.write(json.dumps(d) + "\n")
                    m = evaluate_run_metrics(
                        decisions,
                        arm_a_decisions,
                        cfg,
                        arm_name="cheap_model",
                        config_dict={"confidence_threshold": cc},
                        mode=mode,
                    )
                    all_summaries.append(m)

                # Arm E: Small Classifier Sweeps
                clf_confs = [0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.98]
                for cfc in clf_confs:
                    decisions, train_cost = runner.run_arm_e(
                        confidence_threshold=cfc,
                        retrain_on_drift=flush,
                    )
                    for d in decisions:
                        dec_file.write(json.dumps(d) + "\n")
                    m = evaluate_run_metrics(
                        decisions,
                        arm_a_decisions,
                        cfg,
                        arm_name="small_classifier",
                        config_dict={"confidence_threshold": cfc},
                        mode=mode,
                        meta={"training_cost": train_cost},
                    )
                    all_summaries.append(m)

                # Arm F: Microloop Sweeps
                microloop_configs = [
                    {"comparison_rate": 0.05, "min_confidence": 0.95, "is_default": False},
                    {"comparison_rate": 0.10, "min_confidence": 0.95, "is_default": True},
                    {"comparison_rate": 0.20, "min_confidence": 0.95, "is_default": False},
                    {"comparison_rate": 0.10, "min_confidence": 0.90, "is_default": False},
                    {"comparison_rate": 0.10, "min_confidence": 0.98, "is_default": False},
                ]
                for mlc in microloop_configs:
                    decisions, meta = runner.run_arm_f(
                        comparison_rate=mlc["comparison_rate"],
                        min_confidence=mlc["min_confidence"],
                        explicit_invalidation=flush,
                    )
                    for d in decisions:
                        dec_file.write(json.dumps(d) + "\n")
                    m = evaluate_run_metrics(
                        decisions,
                        arm_a_decisions,
                        cfg,
                        arm_name="microloop",
                        config_dict=mlc,
                        mode=mode,
                        meta=meta,
                    )
                    all_summaries.append(m)

    with open(summary_path, "w") as sum_file:
        json.dump(all_summaries, sum_file, indent=2)

    print(f"\nBenchmark completed successfully!")
    print(f"Total experiment runs evaluated: {len(all_summaries)}")
    print(f"Decisions log saved to: {decisions_log_path}")
    print(f"Summary JSON saved to: {summary_path}")


if __name__ == "__main__":
    main()
