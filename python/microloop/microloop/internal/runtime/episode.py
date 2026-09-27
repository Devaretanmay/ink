"""Execution episode: one agent task, and the adaptation data it produces.

An episode is a stronger primitive than a raw trajectory: it groups the goal,
progress transitions, runtime changes, adaptations and outcome, and carries the
usage needed for the cost-per-successful-task north star. Pass 1 defines and
records the schema; nothing is trained from it yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .action import RuntimeAction
from .outcome import outcome_for
from .state import RuntimeState, Usage

__all__ = ["AdaptationRecord", "Episode"]


@dataclass
class AdaptationRecord:
    """Progress and runtime before an action, and progress after it.

    This is the unit the learning loop will rank actions on: what the run looked
    like, what was done, and whether it helped.
    """

    before: dict[str, Any]
    action: str
    step: int | None = None
    after: dict[str, Any] | None = None
    outcome: str | None = None
    applied: bool = False
    adapter_reason: str | None = None
    #: `improved`, `no_change` or `regressed`, once progress after the action is
    #: known. Distinct from `outcome`, the run's final result.
    result: str | None = None
    #: The controller's candidate scores at the step the action was chosen, when
    #: the decision used the scored strategy. Retained as counterfactual data.
    trace: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "before": self.before,
            "action": self.action,
            "after": self.after,
            "outcome": self.outcome,
            "applied": self.applied,
            "adapter_reason": self.adapter_reason,
            "result": self.result,
        }
        if self.step is not None:
            payload["step"] = self.step
        if self.trace is not None:
            payload["trace"] = self.trace
        return payload


@dataclass
class Episode:
    """One agent task: goal, transitions, adaptations, outcome and usage."""

    goal: str | None = None
    progress_transitions: list[dict[str, Any]] = field(default_factory=list)
    runtime_changes: list[dict[str, Any]] = field(default_factory=list)
    adaptations: list[AdaptationRecord] = field(default_factory=list)
    outcome: str | None = None
    usage: Usage = field(default_factory=Usage)

    _steps: int = field(default=0, init=False, repr=False)
    _last_state: str | None = field(default=None, init=False, repr=False)
    _last_model: str | None = field(default=None, init=False, repr=False)
    _pending: AdaptationRecord | None = field(default=None, init=False, repr=False)

    def record(
        self,
        decision: Any,
        *,
        applied: bool = False,
        adapter_reason: str | None = None,
        tier: str | None = None,
    ) -> None:
        """Fold one decision into the episode."""
        self._steps += 1
        progress = getattr(decision, "progress", None)
        state = progress.state if progress is not None else getattr(decision, "status", "healthy")
        runtime = getattr(decision, "runtime", None) or RuntimeState()
        step = getattr(decision, "step", self._steps)

        def snapshot(progress_state: str) -> dict[str, Any]:
            # ``signals`` travels with the snapshot so the dataset can be
            # segmented by situation, not only by action. "Replan helped" is not
            # a finding; "replan helped when the run was stalled and the error
            # kept recurring" is.
            return {
                "progress": progress_state,
                "signals": list(getattr(progress, "signals", []) or []),
                "runtime": runtime.to_dict(),
                "model_tier": tier,
                "cost": runtime.cost,
            }

        if self._last_state is not None and state != self._last_state:
            self.progress_transitions.append(
                {"step": step, "from": self._last_state, "to": state}
            )
            if self._pending is not None and self._pending.after is None:
                self._pending.after = snapshot(state)
                self._pending.result = outcome_for(self._pending.before["progress"], state)
                self._pending = None
        self._last_state = state

        if runtime.model is not None and runtime.model != self._last_model:
            self.runtime_changes.append({"step": step, "model": runtime.model})
            self._last_model = runtime.model

        message = getattr(decision, "recommendation", None)
        action = getattr(message, "action", RuntimeAction.Continue)
        if action != RuntimeAction.Continue:
            # A new adaptation closes the previous one at the progress it left
            # the run in, so every action gets its own row.
            if self._pending is not None:
                if self._pending.after is None:
                    self._pending.after = snapshot(state)
                    self._pending.result = outcome_for(
                        self._pending.before["progress"], state
                    )
                self._pending.outcome = self.outcome
            trace = getattr(message, "trace", None)
            self._pending = AdaptationRecord(
                before=snapshot(state),
                action=action,
                step=step,
                applied=applied,
                adapter_reason=adapter_reason,
                trace=trace.to_dict() if trace is not None else None,
            )
            self.adaptations.append(self._pending)

        if not _is_empty(runtime):
            self.usage = runtime.usage()

    def finish(self, outcome: str | None = None) -> None:
        """Close the episode, propagating the outcome to any pending adaptation."""
        self.outcome = outcome
        if self._pending is not None:
            self._pending.outcome = outcome

    def summary(self) -> dict[str, Any]:
        """Episode summary, including the cost-per-successful-task metric."""
        completed = self.outcome == "completed"
        return {
            "goal": self.goal,
            "outcome": self.outcome,
            "steps": self._steps,
            "usage": self.usage.to_dict(),
            "progress_transitions": self.progress_transitions,
            "runtime_changes": self.runtime_changes,
            "adaptations": [record.to_dict() for record in self.adaptations],
            "cost_per_success": self.usage.cost if completed else None,
        }

    @classmethod
    def from_decisions(
        cls, decisions: list[Any], *, goal: str | None = None, outcome: str | None = None
    ) -> Episode:
        episode = cls(goal=goal)
        for decision in decisions:
            episode.record(decision)
        episode.finish(outcome)
        return episode


def _is_empty(runtime: RuntimeState) -> bool:
    return all(
        value is None
        for value in (
            runtime.model,
            runtime.context_tokens,
            runtime.cost,
            runtime.input_tokens,
            runtime.output_tokens,
        )
    )
