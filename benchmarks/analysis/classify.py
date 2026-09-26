"""
Trajectory Classifier and Failure Taxonomy Generator (Pass 2.5).
Analyzes raw trajectories and evaluation outcomes to classify runs into:
- successful-efficient
- successful-wasteful
- failed-recoverable
- failed-irrecoverable
And extracts empirical failure mode frequencies.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple


def analyze_single_trajectory(run_dir: str) -> Dict[str, Any]:
    """
    Analyzes an individual run directory containing:
    metadata.json, trajectory.jsonl, evaluation.json
    """
    metadata_path = os.path.join(run_dir, "metadata.json")
    traj_path = os.path.join(run_dir, "trajectory.jsonl")
    eval_path = os.path.join(run_dir, "evaluation.json")

    metadata = {}
    if os.path.exists(metadata_path):
        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)

    evaluation = {}
    if os.path.exists(eval_path):
        with open(eval_path, "r", encoding="utf-8") as f:
            evaluation = json.load(f)

    events: List[Dict[str, Any]] = []
    if os.path.exists(traj_path):
        with open(traj_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    task_id = metadata.get("task_id") or (events[0].get("task_id") if events else "unknown")
    run_id = metadata.get("run_id") or os.path.basename(run_dir)
    resolved = bool(evaluation.get("resolved", False))
    total_steps = len(events)

    # Detect empirical failure signals
    commands = [e.get("action", {}).get("command", "") for e in events]
    outputs = [e.get("observation", {}).get("stdout", "") + e.get("observation", {}).get("stderr", "") for e in events]
    exit_codes = [e.get("observation", {}).get("exit_code", 0) for e in events]
    error_classes = [e.get("observation", {}).get("error_class") for e in events]

    failure_modes_detected = set()

    # 1. Exact Repetition check: same command + same exit code/output back to back
    for i in range(1, len(commands)):
        if commands[i] and commands[i] == commands[i - 1] and exit_codes[i] == exit_codes[i - 1]:
            failure_modes_detected.add("exact_repetition")
            break

    # 2. Normalized Repetition check: commands match after masking paths/numbers
    def normalize_cmd(c: str) -> str:
        c = re.sub(r"/[\w\-./]+", "<PATH>", c)
        c = re.sub(r"\b\d+\b", "<NUM>", c)
        return c.strip()

    norm_cmds = [normalize_cmd(c) for c in commands]
    for i in range(2, len(norm_cmds)):
        if norm_cmds[i] and norm_cmds.count(norm_cmds[i]) >= 3:
            failure_modes_detected.add("normalized_repetition")
            break

    # 3. Error Recurrence: identical error class recurring non-consecutively
    errs = [err for err in error_classes if err]
    for err in set(errs):
        if errs.count(err) >= 2:
            failure_modes_detected.add("error_recurrence")
            break

    # 4. State Stagnation: test failures count unchanged across multiple test commands
    failing_counts = [
        e.get("metrics", {}).get("tests_failed")
        for e in events
        if e.get("metrics", {}).get("tests_failed") is not None
    ]
    if len(failing_counts) >= 2 and all(c == failing_counts[0] for c in failing_counts) and failing_counts[0] > 0:
        failure_modes_detected.add("state_stagnation")

    # 5. Strategy Oscillation: alternating A-B-A-B in edited files or commands
    diff_hashes = [e.get("workspace", {}).get("diff_hash", "") for e in events if e.get("workspace", {}).get("dirty")]
    if len(diff_hashes) >= 4:
        for i in range(len(diff_hashes) - 3):
            if diff_hashes[i] == diff_hashes[i + 2] and diff_hashes[i + 1] == diff_hashes[i + 3] and diff_hashes[i] != diff_hashes[i + 1]:
                failure_modes_detected.add("strategy_oscillation")
                break

    # 6. Tool Thrashing: >= 6 read/grep/find commands in a row without modifying files
    consecutive_reads = 0
    max_consecutive_reads = 0
    for e in events:
        cmd = e.get("action", {}).get("command", "")
        if any(cmd.startswith(prefix) for prefix in ["grep", "find", "cat", "head", "ls"]):
            consecutive_reads += 1
            max_consecutive_reads = max(max_consecutive_reads, consecutive_reads)
        else:
            consecutive_reads = 0
    if max_consecutive_reads >= 6:
        failure_modes_detected.add("tool_thrashing")

    # 7. Budget Exhaustion
    max_steps_allowed = metadata.get("max_steps", 50)
    if total_steps >= max_steps_allowed and not resolved:
        failure_modes_detected.add("budget_exhaustion")

    # Outcome Classification
    if resolved:
        # Check if it was wasteful
        if total_steps > 15 or len(failure_modes_detected) > 0:
            outcome = "successful-wasteful"
        else:
            outcome = "successful-efficient"
    else:
        # Unresolved: check if recoverable
        recoverable_signals = {"exact_repetition", "normalized_repetition", "error_recurrence", "state_stagnation", "strategy_oscillation", "tool_thrashing"}
        if failure_modes_detected & recoverable_signals:
            outcome = "failed-recoverable"
        else:
            outcome = "failed-irrecoverable"
            failure_modes_detected.add("irrecoverable_reasoning_error")

    return {
        "run_id": run_id,
        "task_id": task_id,
        "resolved": resolved,
        "total_steps": total_steps,
        "outcome": outcome,
        "failure_modes": sorted(list(failure_modes_detected)),
    }


def generate_taxonomy(results_dir: str, output_path: str = "benchmarks/analysis/failure-taxonomy-v1.json") -> Dict[str, Any]:
    """
    Scans results directory, analyzes all run directories, and writes taxonomy JSON.
    """
    if not os.path.exists(results_dir):
        raise FileNotFoundError(f"Results dir not found: {results_dir}")

    run_dirs = [
        os.path.join(results_dir, d)
        for d in os.listdir(results_dir)
        if os.path.isdir(os.path.join(results_dir, d)) and not d.startswith(".")
    ]

    analyses = [analyze_single_trajectory(d) for d in run_dirs]

    outcomes = {
        "successful-efficient": 0,
        "successful-wasteful": 0,
        "failed-recoverable": 0,
        "failed-irrecoverable": 0,
    }
    modes = {
        "exact_repetition": 0,
        "normalized_repetition": 0,
        "error_recurrence": 0,
        "state_stagnation": 0,
        "strategy_oscillation": 0,
        "tool_thrashing": 0,
        "tool_error_cascade": 0,
        "budget_exhaustion": 0,
        "irrecoverable_reasoning_error": 0,
    }

    for a in analyses:
        outcomes[a["outcome"]] = outcomes.get(a["outcome"], 0) + 1
        for m in a["failure_modes"]:
            modes[m] = modes.get(m, 0) + 1

    taxonomy = {
        "taxonomy_version": "1.0",
        "total_runs_analyzed": len(analyses),
        "outcome_distribution": outcomes,
        "failure_modes": modes,
        "per_run_analysis": analyses,
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(taxonomy, f, indent=2)

    return taxonomy


def main() -> None:
    parser = argparse.ArgumentParser(description="Microloop Trajectory Classifier")
    parser.add_argument("--results-dir", type=str, default="results")
    parser.add_argument("--output", type=str, default="benchmarks/analysis/failure-taxonomy-v1.json")
    args = parser.parse_args()

    tax = generate_taxonomy(args.results_dir, args.output)
    print("\n" + "=" * 60)
    print("           PASS 2.5: FAILURE TAXONOMY SUMMARY")
    print("=" * 60)
    print(f"Total Runs Analyzed: {tax['total_runs_analyzed']}")
    print("\nOutcome Distribution:")
    for k, v in tax["outcome_distribution"].items():
        pct = (v / max(1, tax['total_runs_analyzed'])) * 100
        print(f"  {k:<24}: {v:>3} ({pct:.1f}%)")
    print("\nEmpirical Failure Modes:")
    for k, v in tax["failure_modes"].items():
        print(f"  {k:<28}: {v:>3}")
    print("=" * 60)
    print(f"Taxonomy saved to {args.output}\n")


if __name__ == "__main__":
    main()
