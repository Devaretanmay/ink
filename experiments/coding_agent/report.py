"""Generate a deterministic summary from experiment telemetry JSONL."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def generate_report(paths: list[Path]) -> dict[str, Any]:
    task_rows = []
    decisions = []
    for path in sorted(paths, key=str):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            (task_rows if record.get("record_type") == "task" else decisions).append(record)

    arms: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in task_rows:
        grouped[row["mode"]].append(row)
    for mode, rows in sorted(grouped.items()):
        arms[mode] = {
            "tasks": len(rows),
            "successful_tasks": sum(row["success"] for row in rows),
            "frontier_calls": sum(row["frontier_calls"] for row in rows),
            "input_tokens": sum(row["input_tokens"] for row in rows),
            "output_tokens": sum(row["output_tokens"] for row in rows),
            "reasoning_tokens": sum(row.get("reasoning_tokens", 0) for row in rows),
            "cache_tokens": sum(row.get("cache_tokens", 0) for row in rows),
            "estimated_model_cost": round(sum(row["estimated_model_cost"] for row in rows), 8),
            "tool_calls": sum(row["tool_calls"] for row in rows),
            "decision_site_calls": sum(row["selected_decision_site_calls"] for row in rows),
            "exact_serves": sum(row["ink_exact_serves"] for row in rows),
            "learned_serves": sum(row["ink_learned_serves"] for row in rows),
            "fallbacks": sum(row["ink_fallbacks"] for row in rows),
            "comparison_calls": sum(row["comparison_calls"] for row in rows),
            "incorrect_serves": sum(row["incorrect_ink_decisions"] for row in rows),
            "wall_clock_seconds": round(sum(row["wall_clock_seconds"] for row in rows), 6),
        }

    candidate = [row for row in decisions if row.get("candidate_correct") is not None]
    return {
        "schema_version": "1",
        "arms": arms,
        "decision_summary": {
            "total": len(decisions),
            "sources": dict(sorted(Counter(row["source"] for row in decisions).items())),
            "fallback_labels": dict(
                sorted(Counter(row["fallback_choice"] for row in decisions).items())
            ),
            "candidate_predictions": len(candidate),
            "candidate_accuracy": (
                sum(row["candidate_correct"] for row in candidate) / len(candidate)
                if candidate
                else None
            ),
            "incorrect_local_serves": sum(row.get("served_correct") is False for row in decisions),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("telemetry", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rendered = json.dumps(generate_report(args.telemetry), indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
