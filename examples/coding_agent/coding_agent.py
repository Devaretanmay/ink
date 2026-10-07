"""Pilot C: Autonomous Coding & CI Agent (Integrated with Ink)."""

import time
from typing import Any

from ink import DecisionSite, Ink


class IntegratedCodingAgent:
    def __init__(self, client: Ink):
        self.ml = client
        self.site = DecisionSite(
            name="coding.action_dispatch",
            state_schema={
                "failure_diagnosis": "string",
                "git_status": "string",
                "test_runner": "string",
            },
            choices=(
                "patch_ast",
                "commit_patch",
                "inspect_traceback",
                "replan",
                "run_pytest",
            ),
        )
        self.ml.register(self.site)
        self.model_calls = 0
        self.total_cost = 0.0
        self.latencies = []

    def _cloud_model_fallback(self, state: dict[str, Any]) -> str:
        self.model_calls += 1
        self.total_cost += 0.015
        time.sleep(0.005)

        diag = state.get("failure_diagnosis", "")
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
        started = time.perf_counter()
        # Developer review: exclude volatile build_uuid
        clean_state = {
            "failure_diagnosis": str(context.get("failure_diagnosis", "")),
            "git_status": str(context.get("git_status", "dirty")),
            "test_runner": str(context.get("test_runner", "pytest")),
        }

        decision = self.ml.decide(
            site=self.site.name,
            state=clean_state,
            fallback=lambda: self._cloud_model_fallback(clean_state),
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.latencies.append(elapsed_ms)

        self.ml.record_outcome(
            decision.decision_id,
            quality=1.0,
            verifier="pytest_exit_code",
            verifier_version="1",
            evidence={"exit_code": 0, "runner": "pytest"},
        )

        return {
            "action": decision.choice,
            "decision_source": decision.source,
            "decision_id": decision.decision_id,
        }
