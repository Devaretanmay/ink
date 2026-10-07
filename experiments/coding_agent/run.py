"""Command-line entry point for paired OpenCode experiments."""

from __future__ import annotations

import argparse
from pathlib import Path

from .opencode import ExperimentConfig, load_tasks, run_experiment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--mode", choices=("control", "observe", "shadow", "active"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ink-db", type=Path, required=True)
    parser.add_argument("--agent", default="build")
    parser.add_argument("--variant")
    parser.add_argument("--task-id", action="append")
    parser.add_argument("--opencode", default="opencode")
    args = parser.parse_args()

    selected = set(args.task_id or ())
    tasks = [task for task in load_tasks(args.manifest) if not selected or task.task_id in selected]
    if not tasks:
        parser.error("No tasks selected")
    config = ExperimentConfig(
        model=args.model,
        mode=args.mode,
        output_dir=args.output_dir,
        ink_db=args.ink_db,
        opencode_executable=args.opencode,
        agent=args.agent,
        variant=args.variant,
    )
    for task in tasks:
        result = run_experiment(task, config)
        print(f"{task.task_id}: {'PASS' if result['success'] else 'FAIL'} ({args.mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
