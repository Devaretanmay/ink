"""
Baseline A: Vanilla Agent Condition.
Executes the base agent without Microloop or retry supervision.
"""
from __future__ import annotations

from typing import Any, Dict


def run_vanilla_trial(
    task: Dict[str, Any],
    model: str,
    seed: int,
    max_steps: int = 50,
) -> Dict[str, Any]:
    """Runs a single trial under Condition A (Vanilla)."""
    return {
        "task_id": task["task_id"],
        "condition": "vanilla",
        "model": model,
        "seed": seed,
        "max_steps": max_steps,
        "interventions_enabled": False,
    }
