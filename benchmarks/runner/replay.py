"""
Microloop Offline Trajectory Replay Tool.
Replays raw trajectory.jsonl files through the Rust trajectory engine
without invoking external LLMs.

Usage:
    python -m benchmarks.runner.replay path/to/trajectory.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from benchmarks.runner.bridge import (
    decision_to_dict,
    intervention_of,
    monitor_for,
    observe_canonical,
)


def replay_trajectory(
    trajectory_path: str,
    window: int = 32,
    repetitions: int = 3,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    Replays a raw trajectory.jsonl file through Microloop Monitor.
    Returns analysis summary.
    """
    if not os.path.exists(trajectory_path):
        raise FileNotFoundError(f"Trajectory file not found: {trajectory_path}")

    events = []
    with open(trajectory_path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"[Warning] Skipping malformed JSON on line {line_no}: {e}", file=sys.stderr)

    if not events:
        print("[Replay] No events found in trajectory file.")
        return {"total_steps": 0, "decisions": []}

    run_id = events[0].get("run_id", "replay_run")
    monitor = monitor_for(window=window, repetitions=repetitions)

    decisions_log = []
    stalls = 0
    warnings = 0
    regressions = 0
    first_detection_step: int | None = None

    if verbose:
        print(f"\n{'=' * 70}")
        print(f"       MICROLOOP TRAJECTORY REPLAY: {os.path.basename(trajectory_path)}")
        print(f"       Run ID: {run_id} | Total Steps: {len(events)}")
        print(f"{'=' * 70}\n")

    for event in events:
        step = event.get("step", 0)
        command = (event.get("action") or {}).get("command", "")

        decision = observe_canonical(monitor, event)
        decisions_log.append(decision_to_dict(decision))
        intervention = intervention_of(decision)

        state = decision.status.upper()
        score = decision.severity
        evidence_str = f" [{', '.join(decision.reasons)}]" if decision.reasons else ""

        if state == "WARNING":
            warnings += 1
            if first_detection_step is None:
                first_detection_step = step
        elif state == "STALLED":
            stalls += 1
            if first_detection_step is None:
                first_detection_step = step
        elif state == "REGRESSING":
            regressions += 1
            if first_detection_step is None:
                first_detection_step = step

        if verbose:
            tag = f"step {step:02d}"
            cmd_preview = (command[:32] + "...") if len(command) > 35 else command
            print(f"{tag:<8} | {state:<10} (score: {score:.2f}) | {cmd_preview:<35}{evidence_str}")
            if intervention.get("kind") == "replan":
                print(f"         └─ INTERVENTION: REPLAN triggered at step {step}")

    if verbose:
        print(f"\n{'-' * 70}")
        print("REPLAY SUMMARY:")
        print(f"  Total Steps:            {len(events)}")
        print(f"  Warning Steps:          {warnings}")
        print(f"  Stalled Steps:          {stalls}")
        print(f"  Regressing Steps:       {regressions}")
        print(f"  First Detection Step:   {first_detection_step or 'None (Trajectory Healthy)'}")
        print(f"{'=' * 70}\n")

    return {
        "run_id": run_id,
        "total_steps": len(events),
        "warnings": warnings,
        "stalls": stalls,
        "regressions": regressions,
        "first_detection_step": first_detection_step,
        "decisions": decisions_log,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Microloop Offline Trajectory Replay Tool")
    parser.add_argument("trajectory_file", type=str, help="Path to raw trajectory.jsonl")
    parser.add_argument("--window", type=int, default=32, help="Monitor sliding window size")
    parser.add_argument("--repetitions", type=int, default=3, help="Repetition threshold")
    parser.add_argument("--json", action="store_true", help="Output summary in JSON format")
    args = parser.parse_args()

    summary = replay_trajectory(
        trajectory_path=args.trajectory_file,
        window=args.window,
        repetitions=args.repetitions,
        verbose=not args.json,
    )

    if args.json:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
