"""Pilot A: Autonomous Agent Tool Orchestrator (Integrated with Microloop)."""

import time
from typing import Any

from microloop import DecisionSite, Microloop


class IntegratedAgentOrchestrator:
    def __init__(self, client: Microloop):
        self.ml = client
        self.site = DecisionSite(
            name="agent.tool_selector",
            state_schema={"intent": "string", "step": "integer"},
            choices=("bash", "read_file", "finish", "ask_user", "web_search"),
        )
        self.ml.register(self.site)
        self.model_calls = 0
        self.total_cost = 0.0
        self.latencies = []

    def _cloud_model_fallback(self, state: dict[str, Any]) -> str:
        self.model_calls += 1
        self.total_cost += 0.0032
        time.sleep(0.005)
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

    def execute_step(
        self,
        task_context: dict[str, Any],
        interrupted: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        # Developer review: exclude volatile 'request_id' from state contract
        clean_state = {
            "intent": str(task_context.get("intent", "")),
            "step": int(task_context.get("step", 1)),
        }

        decision = self.ml.decide(
            site=self.site.name,
            state=clean_state,
            fallback=lambda: self._cloud_model_fallback(clean_state),
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.latencies.append(elapsed_ms)

        tool = decision.choice
        success = True

        if not interrupted:
            self.ml.record_outcome(
                decision.decision_id,
                quality=1.0 if success else 0.0,
                verifier="tool_execution",
                verifier_version="1",
                evidence={"exit_code": 0, "tool": tool},
            )

        return {
            "tool": tool,
            "success": success,
            "decision_source": decision.source,
            "decision_id": decision.decision_id,
        }
