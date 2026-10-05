from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ink import DecisionSite, FallbackResult, Ink

from .events import AgentEvent, TrajectoryWindow
from .features import extract_trajectory_features
from .outcomes import evaluate_progress_resumed
from .recovery import ContextPatch, LocalRetrievalAdapter

ALLOWED_RECOVERY_CHOICES = ("continue", "retrieve_context", "replan", "escalate")

CODING_AGENT_STATE_SCHEMA = {
    "same_error_count": "integer",
    "failed_command_streak": "integer",
    "test_failure_streak": "integer",
    "repeated_search_count": "integer",
    "repeated_file_read_count": "integer",
    "search_query_repeat_count": "integer",
    "same_file_edit_count": "integer",
    "edit_revert_count": "integer",
    "unique_files_read": "integer",
    "unique_files_edited": "integer",
    "steps_since_new_file": "integer",
    "steps_since_new_evidence": "integer",
    "steps_since_test_improvement": "integer",
    "recent_test_delta": "integer",
    "current_error_signature": "string",
}


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    choice: str
    source: str
    decision_id: str
    fallback_reason: str | None
    state: dict[str, Any]
    context_patch: ContextPatch | None = None


class CodingAgentRecoveryAdapter:
    def __init__(
        self,
        client: Ink,
        repo_path: Path | str = ".",
        site_name: str = "coding_agent.recovery_action",
        window_size: int = 30,
    ) -> None:
        self.client = client
        self.repo_path = Path(repo_path).resolve()
        self.site = DecisionSite(
            name=site_name,
            state_schema=CODING_AGENT_STATE_SCHEMA,
            choices=ALLOWED_RECOVERY_CHOICES,
        )
        self.client.register(self.site)
        self.window = TrajectoryWindow(max_size=window_size)
        self.retrieval = LocalRetrievalAdapter(self.repo_path)

    def record_event(self, event: AgentEvent) -> None:
        self.window.append(event)

    def default_teacher_policy(self, state: dict[str, Any]) -> str:
        if state["same_error_count"] >= 4:
            return "escalate"
        if state["failed_command_streak"] >= 2 or state["test_failure_streak"] >= 2:
            if state["same_file_edit_count"] >= 3 or state["edit_revert_count"] >= 1:
                return "replan"
            return "retrieve_context"
        if state["repeated_search_count"] >= 2 or state["steps_since_new_evidence"] >= 10:
            return "retrieve_context"
        return "continue"

    def decide(
        self,
        fallback_fn: Callable[[], str | FallbackResult] | None = None,
        task_id: str | None = None,
    ) -> RecoveryDecision:
        events = self.window.events()
        state = extract_trajectory_features(events)

        def _fallback() -> FallbackResult:
            if fallback_fn is not None:
                res = fallback_fn()
                choice = res.choice if isinstance(res, FallbackResult) else str(res)
            else:
                choice = self.default_teacher_policy(state)
            if choice not in ALLOWED_RECOVERY_CHOICES:
                raise ValueError(f"Disallowed recovery action: {choice!r}")
            return FallbackResult(choice=choice, model_calls=1)

        decision = self.client.decide(
            site=self.site,
            state=state,
            task_id=task_id,
            fallback=_fallback,
        )

        if decision.choice not in ALLOWED_RECOVERY_CHOICES:
            raise ValueError(f"Non-destructive recovery invariant violated: {decision.choice}")

        patch = None
        if decision.choice == "retrieve_context":
            err_sig = state.get("current_error_signature", "")
            query = err_sig if err_sig and err_sig != "None" else "error"
            question = f"Where is {query} handled or defined?"
            patch = self.retrieval.retrieve(
                question=question, query=query, reason="agent_stagnation"
            )

        return RecoveryDecision(
            choice=decision.choice,
            source=decision.source,
            decision_id=decision.decision_id,
            fallback_reason=decision.fallback_reason,
            state=state,
            context_patch=patch,
        )

    def record_outcome(
        self,
        decision_id: str,
        before_events: list[AgentEvent],
        after_events: list[AgentEvent],
        eval_window: int = 10,
    ) -> tuple[float | None, str]:
        score, reason, details = evaluate_progress_resumed(
            before_events, after_events, window_size=eval_window
        )
        self.client.record_outcome(
            decision_id,
            quality=score,
            verifier="progress_resumed_v1",
            verifier_version="1",
            evidence=details,
        )
        return score, reason
