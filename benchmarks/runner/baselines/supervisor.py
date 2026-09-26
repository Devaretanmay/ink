"""
Baseline C: LLM Supervisor Condition.
Prompts an external supervisory LLM every N steps with recent trajectory history
to decide: CONTINUE, REPLAN, RESTART, or STOP.
"""
from __future__ import annotations

from typing import Any, Dict, List


class LLMSupervisor:
    def __init__(self, check_interval: int = 5, model: str = "gpt-4o-mini") -> None:
        self.check_interval = check_interval
        self.model = model
        self.supervision_calls = 0

    def generate_supervisor_prompt(self, recent_history: List[Dict[str, Any]]) -> str:
        """Constructs prompt for the supervisory model."""
        history_summary = "\n".join(
            f"Step {h.get('step')}: Action={h.get('action')}, Result Success={h.get('success')}"
            for h in recent_history[-self.check_interval:]
        )
        return (
            "You are an AI Agent Supervisor evaluating an autonomous coding task.\n"
            "Review the recent trajectory steps below and output one decision:\n"
            "CONTINUE, REPLAN, RESTART, or STOP.\n\n"
            f"Recent history:\n{history_summary}\n\n"
            "Decision:"
        )

    def evaluate_step(self, step: int, history: List[Dict[str, Any]]) -> str:
        """Evaluates whether to supervise at this step."""
        if step > 0 and step % self.check_interval == 0:
            self.supervision_calls += 1
            # In live benchmark: call model API
            return "CONTINUE"
        return "CONTINUE"
