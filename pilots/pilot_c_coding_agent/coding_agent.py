"""Pilot C: Autonomous Coding & CI Agent (Before Microloop)."""

import time
from typing import Any


class UninstrumentedCodingAgent:
    def __init__(self):
        self.model_calls = 0
        self.total_cost = 0.0
        self.latencies = []

    def mock_llm_decide_action(self, context: dict[str, Any]) -> str:
        self.model_calls += 1
        self.total_cost += 0.015  # High-cost reasoning model call
        time.sleep(0.005)
        self.latencies.append(1200.0)

        diag = context.get("failure_diagnosis", "")
        if "AssertionError" in diag:
            return "inspect_traceback"
        if "TypeError" in diag or "SyntaxError" in diag:
            return "patch_ast"
        if "passing cleanly" in diag:
            return "commit_patch"
        if "cyclic" in diag:
            return "replan"
        return "run_pytest"

    def execute_ci_repair_step(self, context: dict[str, Any]) -> dict[str, Any]:
        action = self.mock_llm_decide_action(context)
        return {"action": action, "build_uuid": context.get("build_uuid")}
