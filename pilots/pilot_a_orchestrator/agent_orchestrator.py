"""Pilot A: Autonomous Agent Tool Orchestrator (Before Microloop)."""

import time
from typing import Any


class UninstrumentedAgentOrchestrator:
    def __init__(self):
        self.model_calls = 0
        self.total_cost = 0.0
        self.latencies = []

    def mock_llm_tool_select(self, state: dict[str, Any]) -> str:
        self.model_calls += 1
        self.total_cost += 0.0032
        lat = 0.250  # 250ms simulated cloud inference
        time.sleep(0.005)  # slight real delay
        self.latencies.append(lat * 1000)

        intent = state.get("intent", "")
        if "find_file" in intent:
            return "read_file"
        if "test_suite" in intent or "diff" in intent:
            return "bash"
        if "documentation" in intent:
            return "web_search"
        if "clarify" in intent:
            return "ask_user"
        return "finish"

    def execute_step(self, task_context: dict[str, Any]) -> dict[str, Any]:
        tool = self.mock_llm_tool_select(task_context)
        # Simulated tool execution:
        success = True
        return {"tool": tool, "success": success, "step": task_context.get("step", 1)}
