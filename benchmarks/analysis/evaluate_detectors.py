"""
Microloop Offline Detector Evaluation Benchmark (Pass 3).

Replays labeled baseline trajectories through the Rust trajectory engine
and measures:
- Precision on non-progress runs
- Recall on failed-recoverable and successful-wasteful runs
- Median detection delay (steps from loop manifestation to detection)
- False alarm rate per 100 steps on successful-efficient runs
- Per-detector activation breakdown (D1, D2, D3, D5)

Usage:
    python -m benchmarks.analysis.evaluate_detectors [--results-dir results] [--output benchmarks/analysis/detector-evaluation-v1.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from typing import Any, Dict, List, Optional, Tuple

from benchmarks.analysis.classify import analyze_single_trajectory
from benchmarks.agents.mini_swe.events import hash_text, mask_volatile_noise_py
from microloop import Monitor, Policy

mask_noise_py = mask_volatile_noise_py


def evaluate_trajectory_run(
    run_dir: str,
    window: int = 32,
    repetitions: int = 3,
    stagnation_steps: int = 8,
) -> Dict[str, Any]:
    """
    Evaluates a single run directory by comparing trajectory classification
    with offline detector performance.
    """
    # 1. Ground truth classification
    traj_analysis = analyze_single_trajectory(run_dir)
    ground_truth_outcome = traj_analysis["outcome"]
    failure_modes = traj_analysis["failure_modes"]
    is_positive = ground_truth_outcome in ("failed-recoverable", "successful-wasteful")

    traj_path = os.path.join(run_dir, "trajectory.jsonl")
    if not os.path.exists(traj_path):
        return {
            "run_id": os.path.basename(run_dir),
            "skipped": True,
            "reason": "Missing trajectory.jsonl",
        }

    # 2. Replay through Monitor
    config_dict = {
        "window": window,
        "repetitions": repetitions,
        "stagnation_steps": stagnation_steps,
        "verification_samples": 3,
        "normalize_actions": True,
    }
    config_json = json.dumps(config_dict)
    run_id = traj_analysis["run_id"]
    monitor = Monitor(run_id=run_id, config_json=config_json)
    policy = Policy(config_json=json.dumps({"replan": True, "cooldown_steps": 3, "max_replans": 3}))

    events: List[Dict[str, Any]] = []
    with open(traj_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    total_steps = len(events)
    first_detection_step: Optional[int] = None
    first_stalled_step: Optional[int] = None
    detector_activations: Dict[str, int] = {
        "repeated_action_result": 0,
        "normalized_repetition": 0,
        "repeated_error": 0,
        "state_stagnation": 0,
        "state_oscillation": 0,
        "regression": 0,
    }
    interventions_count = 0
    false_alarms = 0

    for event in events:
        step = event.get("step", 0)
        action_data = event.get("action", {})
        command = action_data.get("command", "")
        obs_data = event.get("observation", {})
        exit_code = obs_data.get("exit_code", 0)
        stdout = obs_data.get("stdout", "")
        stderr = obs_data.get("stderr", "")
        combined_obs = stdout + stderr
        error_class = obs_data.get("error_class")

        action_hash = hash_text(command)
        norm_action_hash = hash_text(mask_noise_py(command))
        obs_hash = hash_text(combined_obs)
        norm_obs_hash = hash_text(mask_noise_py(combined_obs))

        err_fp = f"{error_class}:{action_hash}" if exit_code != 0 and error_class else (
            f"Exit{exit_code}:{action_hash}" if exit_code != 0 else None
        )

        metrics = event.get("metrics", {})
        failures = metrics.get("tests_failed")
        scope = metrics.get("verification_scope") or "pytest"

        rust_event = {
            "schema_version": 1,
            "run_id": run_id,
            "step": step,
            "action": {
                "name": action_data.get("type", "shell"),
                "fingerprint": action_hash,
                "normalized_fingerprint": norm_action_hash,
            },
            "observation": {
                "success": exit_code == 0,
                "fingerprint": obs_hash,
                "error_fingerprint": err_fp,
                "normalized_fingerprint": norm_obs_hash,
            },
            "verification": (
                {
                    "scope": scope,
                    "observation_id": f"obs_{step}_{obs_hash[:8]}",
                    "failures": failures,
                }
                if failures is not None
                else None
            ),
            "state_fingerprint": event.get("workspace", {}).get("diff_hash"),
        }

        try:
            decision = monitor.observe(rust_event)
            intervention = policy.apply(decision)
        except Exception:
            continue

        state = decision.get("state", "healthy").lower()
        evidence_list = decision.get("evidence", [])

        if evidence_list:
            for ev in evidence_list:
                reason = ev.get("reason", "")
                if reason in detector_activations:
                    detector_activations[reason] += 1

        if state in ("stalled", "warning") and evidence_list:
            if first_detection_step is None:
                first_detection_step = step
            if state == "stalled" and first_stalled_step is None:
                first_stalled_step = step

        if intervention.get("kind") in ("replan", "stop"):
            interventions_count += 1
            if ground_truth_outcome == "successful-efficient":
                false_alarms += 1

    detected = first_detection_step is not None

    # Calculate detection delay: difference between step where loop first became evident and detection
    detection_delay: Optional[int] = None
    if detected:
        # Approximate loop start: earliest failure mode step or repetition index
        loop_start_step = 1
        for i, ev in enumerate(events):
            if ev.get("observation", {}).get("exit_code", 0) != 0 or ev.get("metrics", {}).get("tests_failed"):
                loop_start_step = ev.get("step", 1)
                break
        detection_delay = max(0, first_detection_step - loop_start_step)

    return {
        "run_id": run_id,
        "task_id": traj_analysis["task_id"],
        "ground_truth_outcome": ground_truth_outcome,
        "is_positive": is_positive,
        "failure_modes": list(failure_modes),
        "total_steps": total_steps,
        "detected": detected,
        "first_detection_step": first_detection_step,
        "first_stalled_step": first_stalled_step,
        "detection_delay": detection_delay,
        "detector_activations": detector_activations,
        "interventions_count": interventions_count,
        "false_alarms": false_alarms,
    }


def run_detector_benchmark(
    results_dir: str = "results",
    window: int = 32,
    repetitions: int = 3,
    stagnation_steps: int = 8,
) -> Dict[str, Any]:
    """
    Runs detector evaluation over all run directories in results_dir.
    """
    if not os.path.exists(results_dir):
        raise FileNotFoundError(f"Results directory does not exist: {results_dir}")

    run_dirs = sorted([
        os.path.join(results_dir, d)
        for d in os.listdir(results_dir)
        if os.path.isdir(os.path.join(results_dir, d)) and d.startswith("run_")
    ])

    evaluations: List[Dict[str, Any]] = []
    for r_dir in run_dirs:
        ev = evaluate_trajectory_run(
            r_dir,
            window=window,
            repetitions=repetitions,
            stagnation_steps=stagnation_steps,
        )
        if not ev.get("skipped"):
            evaluations.append(ev)

    total_runs = len(evaluations)
    if total_runs == 0:
        return {"error": "No valid runs found to evaluate."}

    # Confusion matrix calculations
    tp = 0
    fp = 0
    fn = 0
    tn = 0
    delays: List[int] = []
    clean_steps_total = 0
    clean_false_alarms_total = 0

    detector_totals = {
        "repeated_action_result": 0,
        "normalized_repetition": 0,
        "repeated_error": 0,
        "state_stagnation": 0,
        "state_oscillation": 0,
        "regression": 0,
    }

    for ev in evaluations:
        is_pos = ev["is_positive"]
        detected = ev["detected"]

        for det, count in ev["detector_activations"].items():
            detector_totals[det] += count

        if is_pos and detected:
            tp += 1
            if ev["detection_delay"] is not None:
                delays.append(ev["detection_delay"])
        elif not is_pos and detected:
            fp += 1
        elif is_pos and not detected:
            fn += 1
        else:
            tn += 1

        if ev["ground_truth_outcome"] == "successful-efficient":
            clean_steps_total += ev["total_steps"]
            clean_false_alarms_total += ev["false_alarms"]

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    median_delay = float(statistics.median(delays)) if delays else 0.0
    far_per_100_steps = (clean_false_alarms_total / clean_steps_total * 100.0) if clean_steps_total > 0 else 0.0

    return {
        "dataset_summary": {
            "total_runs_evaluated": total_runs,
            "ground_truth_positives": tp + fn,
            "ground_truth_negatives": fp + tn,
            "total_clean_steps": clean_steps_total,
        },
        "confusion_matrix": {
            "true_positives": tp,
            "false_positives": fp,
            "true_negatives": tn,
            "false_negatives": fn,
        },
        "performance_metrics": {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1, 4),
            "median_detection_delay_steps": median_delay,
            "false_alarm_rate_per_100_steps": round(far_per_100_steps, 4),
        },
        "detector_activations": {
            "D1_repeated_action_result": detector_totals["repeated_action_result"],
            "D2_normalized_repetition": detector_totals["normalized_repetition"],
            "D3_repeated_error": detector_totals["repeated_error"],
            "D5_state_stagnation": detector_totals["state_stagnation"],
            "D4_state_oscillation": detector_totals["state_oscillation"],
            "D6_regression": detector_totals["regression"],
        },
        "runs": evaluations,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate Microloop Trajectory Detectors Offline")
    parser.add_argument("--results-dir", default="results", help="Directory containing run results")
    parser.add_argument("--window", type=int, default=32, help="Monitor window size")
    parser.add_argument("--repetitions", type=int, default=3, help="Repetition threshold")
    parser.add_argument("--stagnation-steps", type=int, default=8, help="Stagnation step threshold")
    parser.add_argument("--output", default="benchmarks/analysis/detector-evaluation-v1.json", help="Path to write evaluation report")
    parser.add_argument("--json", action="store_true", help="Print json output only")

    args = parser.parse_args()

    results = run_detector_benchmark(
        results_dir=args.results_dir,
        window=args.window,
        repetitions=args.repetitions,
        stagnation_steps=args.stagnation_steps,
    )

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

    if args.json:
        print(json.dumps(results, indent=2))
        return

    # Print human-readable report
    metrics = results.get("performance_metrics", {})
    cm = results.get("confusion_matrix", {})
    summary = results.get("dataset_summary", {})
    activations = results.get("detector_activations", {})

    print("\n" + "=" * 70)
    print("        MICROLOOP PASS 3: OFFLINE DETECTOR EVALUATION REPORT")
    print("=" * 70)
    print(f"Total Trajectories Evaluated : {summary.get('total_runs_evaluated')}")
    print(f"Ground Truth Positives (Loops): {summary.get('ground_truth_positives')}")
    print(f"Ground Truth Negatives (Clean): {summary.get('ground_truth_negatives')}")
    print("-" * 70)
    print(f"Precision                    : {metrics.get('precision', 0.0) * 100:.1f}%")
    print(f"Recall                       : {metrics.get('recall', 0.0) * 100:.1f}%")
    print(f"F1 Score                     : {metrics.get('f1_score', 0.0):.4f}")
    print(f"Median Detection Delay       : {metrics.get('median_detection_delay_steps', 0.0):.1f} steps")
    print(f"False Alarm Rate / 100 Steps : {metrics.get('false_alarm_rate_per_100_steps', 0.0):.2f}%")
    print("-" * 70)
    print(f"Confusion Matrix: TP={cm.get('true_positives')}, FP={cm.get('false_positives')}, TN={cm.get('true_negatives')}, FN={cm.get('false_negatives')}")
    print("-" * 70)
    print("Detector Activations across Corpus:")
    for det, count in activations.items():
        print(f"  • {det:30s}: {count}")
    print("=" * 70)
    if args.output:
        print(f"Report written to: {args.output}\n")


if __name__ == "__main__":
    main()
