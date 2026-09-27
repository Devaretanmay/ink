"""Progress snapshot and runtime decision types.

The controller operates on these rather than parsing a legacy ``Decision``.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .action import RuntimeAction

__all__ = [
    "ActionScore",
    "ControllerTrace",
    "ProgressSnapshot",
    "RecommendationReason",
    "RuntimeDecision",
    "Strategy",
]


class RecommendationReason:
    """Why a runtime action was recommended."""

    Progressing = "progressing"
    NoEvidence = "no_evidence"
    ObservationOnly = "observation_only"
    TrajectoryStalled = "trajectory_stalled"
    TrajectoryRegressing = "trajectory_regressing"
    ContextPressure = "context_pressure"
    ProgressRecovered = "progress_recovered"
    BudgetExhausted = "budget_exhausted"
    StepLimitReached = "step_limit_reached"
    ActionUnsupported = "action_unsupported"
    ActionExhausted = "action_exhausted"


class Strategy:
    """How the controller chooses among candidate actions."""

    Rule = "rule"
    Scored = "scored"


@dataclass
class ProgressSnapshot:
    """A clean read of trajectory progress for one step."""

    state: str = "healthy"
    signals: list[str] = field(default_factory=list)
    step: int = 0
    since_step: int = 0
    verification_delta: int | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> ProgressSnapshot:
        if not payload:
            return cls()
        delta = payload.get("verification_delta")
        return cls(
            state=str(payload.get("state", "healthy")),
            signals=[str(signal) for signal in payload.get("signals", [])],
            step=int(payload.get("step", 0)),
            since_step=int(payload.get("since_step", 0)),
            verification_delta=None if delta is None else int(delta),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "state": self.state,
            "signals": list(self.signals),
            "step": self.step,
            "since_step": self.since_step,
        }
        if self.verification_delta is not None:
            payload["verification_delta"] = self.verification_delta
        return payload

    @property
    def degraded(self) -> bool:
        """True when the run is not progressing."""
        return self.state != "healthy"


@dataclass
class ActionScore:
    """The score the controller gave one candidate action."""

    action: str = RuntimeAction.Continue
    score: float = 0.0
    expected_progress: float = 0.0
    cost_penalty: float = 0.0
    repetition_penalty: float = 0.0
    risk_penalty: float = 0.0
    reason: str = RecommendationReason.NoEvidence
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> ActionScore:
        if not payload:
            return cls()
        return cls(
            action=str(payload.get("action", RuntimeAction.Continue)),
            score=float(payload.get("score", 0.0)),
            expected_progress=float(payload.get("expected_progress", 0.0)),
            cost_penalty=float(payload.get("cost_penalty", 0.0)),
            repetition_penalty=float(payload.get("repetition_penalty", 0.0)),
            risk_penalty=float(payload.get("risk_penalty", 0.0)),
            reason=str(payload.get("reason", RecommendationReason.NoEvidence)),
            notes=[str(note) for note in payload.get("notes", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "score": self.score,
            "expected_progress": self.expected_progress,
            "cost_penalty": self.cost_penalty,
            "repetition_penalty": self.repetition_penalty,
            "risk_penalty": self.risk_penalty,
            "reason": self.reason,
            "notes": list(self.notes),
        }


@dataclass
class ControllerTrace:
    """The candidates considered for one decision, and the one selected."""

    strategy: str = Strategy.Rule
    candidates: list[ActionScore] = field(default_factory=list)
    selected: str = RuntimeAction.Continue

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> ControllerTrace:
        if not payload:
            return cls()
        return cls(
            strategy=str(payload.get("strategy", Strategy.Rule)),
            candidates=[
                ActionScore.from_dict(candidate)
                for candidate in payload.get("candidates", [])
            ],
            selected=str(payload.get("selected", RuntimeAction.Continue)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "selected": self.selected,
        }


@dataclass
class RuntimeDecision:
    """The controller's recommendation for a step."""

    action: str = RuntimeAction.Continue
    reason: str = RecommendationReason.NoEvidence
    trace: ControllerTrace | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> RuntimeDecision:
        if not payload:
            return cls()
        trace = payload.get("trace")
        return cls(
            action=str(payload.get("action", RuntimeAction.Continue)),
            reason=str(payload.get("reason", RecommendationReason.NoEvidence)),
            trace=ControllerTrace.from_dict(trace) if trace is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"action": self.action, "reason": self.reason}
        if self.trace is not None:
            payload["trace"] = self.trace.to_dict()
        return payload

    @property
    def should_intervene(self) -> bool:
        return self.action != RuntimeAction.Continue

    @property
    def is_experimental(self) -> bool:
        return RuntimeAction.is_experimental(self.action)
