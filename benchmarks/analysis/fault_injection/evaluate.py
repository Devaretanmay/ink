"""
Comparative Evaluation Benchmark for Deterministic Fault Injection Suite (Pass 5).

Compares:
1. Microloop Local Trajectory Engine (0 LLM tokens, Rust-native deterministic monitoring)
2. External LLM Supervisor (prompts external model every 5 steps)
3. Naive Retry (retries failing actions on error)

Measures:
- Detection Recall on Injected Faults
- Median and Mean Detection Latency (steps from fault injection to detection)
- Total Monitoring & Intervention Token Cost (USD & tokens)
- Granular Category-by-Category Detection Matrix

Usage:
    python -m benchmarks.analysis.fault_injection.evaluate [--output benchmarks/analysis/fault-injection-evaluation-v1.json]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional

from benchmarks.analysis.fault_injection.runner import run_fault_injection_suite


def evaluate_fault_injection(manifest_path: str = "benchmarks/manifests/fault-injection-v1.json") -> Dict[str, Any]:
    """Runs fault injection suite and computes comparative metrics."""
    suite_data = run_fault_injection_suite(manifest_path)
    scenarios = suite_data.get("scenarios", [])
    total = len(scenarios)

    approaches = ["microloop", "supervisor", "retry"]
    stats: Dict[str, Dict[str, Any]] = {
        app: {
            "detected_count": 0,
            "latencies": [],
            "total_tokens_spent": 0,
            "category_performance": defaultdict(lambda: {"total": 0, "detected": 0}),
        }
        for app in approaches
    }

    for sc in scenarios:
        category = sc["category"]
        for app in approaches:
            res = sc.get(app, {})
            stats[app]["category_performance"][category]["total"] += 1
            if res.get("detected"):
                stats[app]["detected_count"] += 1
                stats[app]["category_performance"][category]["detected"] += 1
                lat = res.get("detection_latency_steps")
                if lat is not None:
                    stats[app]["latencies"].append(lat)
            stats[app]["total_tokens_spent"] += res.get("tokens_spent", 0)

    summary = {}
    for app in approaches:
        det = stats[app]["detected_count"]
        recall = det / total if total > 0 else 0.0
        lats = stats[app]["latencies"]
        med_lat = float(statistics.median(lats)) if lats else 0.0
        mean_lat = float(statistics.mean(lats)) if lats else 0.0
        tokens = stats[app]["total_tokens_spent"]
        cost_est = round(0.002 * tokens / 1000, 4)

        cat_breakdown = {}
        for cat, c_data in stats[app]["category_performance"].items():
            cat_tot = c_data["total"]
            cat_det = c_data["detected"]
            cat_breakdown[cat] = {
                "detected": cat_det,
                "total": cat_tot,
                "recall_pct": round(cat_det / cat_tot * 100, 1) if cat_tot > 0 else 0.0,
            }

        summary[app] = {
            "scenarios_evaluated": total,
            "faults_detected": det,
            "detection_recall_pct": round(recall * 100, 2),
            "median_detection_latency_steps": med_lat,
            "mean_detection_latency_steps": round(mean_lat, 2),
            "total_tokens_spent": tokens,
            "estimated_cost_usd": cost_est,
            "category_breakdown": cat_breakdown,
        }

    return {
        "suite": suite_data.get("suite"),
        "total_scenarios": total,
        "comparative_summary": summary,
        "scenarios": scenarios,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate Deterministic Fault Injection Suite")
    parser.add_argument("--manifest", default="benchmarks/manifests/fault-injection-v1.json", help="Path to manifest")
    parser.add_argument("--output", default="benchmarks/analysis/fault-injection-evaluation-v1.json", help="Path to output JSON")
    parser.add_argument("--json", action="store_true", help="Print json output only")
    args = parser.parse_args()

    evaluation = evaluate_fault_injection(args.manifest)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(evaluation, f, indent=2)

    if args.json:
        print(json.dumps(evaluation, indent=2))
        return

    # Print formatted comparative report
    print("\n" + "=" * 78)
    print("      MICROLOOP PASS 5: DETERMINISTIC FAULT INJECTION BENCHMARK REPORT")
    print("=" * 78)
    print(f"Total Fault Scenarios Evaluated: {evaluation['total_scenarios']} (across 10 failure categories)")
    print("-" * 78)
    print(f"{'Approach':18s} | {'Detected':9s} | {'Recall':8s} | {'Median Latency':16s} | {'Token Cost':10s}")
    print("-" * 78)

    summary = evaluation["comparative_summary"]
    for app in ("microloop", "supervisor", "retry"):
        d = summary[app]
        lat_str = f"{d['median_detection_latency_steps']:.1f} steps"
        tok_str = f"{d['total_tokens_spent']:,} tok"
        print(
            f"{app:18s} | {d['faults_detected']:2d} / {d['scenarios_evaluated']:2d}  | "
            f"{d['detection_recall_pct']:6.1f}% | {lat_str:16s} | {tok_str:10s}"
        )
    print("-" * 78)

    print("\nCategory-by-Category Recall Matrix:")
    categories = sorted(list(summary["microloop"]["category_breakdown"].keys()))
    print(f"{'Category':26s} | {'Microloop':10s} | {'Supervisor':10s} | {'Retry':10s}")
    print("-" * 65)
    for cat in categories:
        m_rec = summary["microloop"]["category_breakdown"][cat]["recall_pct"]
        s_rec = summary["supervisor"]["category_breakdown"][cat]["recall_pct"]
        r_rec = summary["retry"]["category_breakdown"][cat]["recall_pct"]
        print(f"{cat:26s} | {m_rec:8.1f}% | {s_rec:8.1f}% | {r_rec:8.1f}%")

    print("=" * 78)
    if args.output:
        print(f"Report written to: {args.output}\n")


if __name__ == "__main__":
    main()
