"""The result types the learning loop will be built on.

``AdaptationResult`` is the full record of one applied action: what the run
looked like before, what it looked like after, and whether progress improved.
``TaskOutcome`` is the host's verdict on the whole task, which is the ground
truth the adaptation outcomes are eventually judged against.

Nothing is trained from these. They are collected so that later passes can
compute empirical statistics about which adaptations actually help.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["AdaptationResult", "TaskOutcome"]


@dataclass
class AdaptationResult:
    """One adaptation: before, the action, after, and the outcome."""

    action: str
    step: int | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    outcome: str | None = None
    applied: bool = False
    adapter_reason: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> AdaptationResult:
        if not payload:
            return cls(action="continue")
        return cls(
            action=str(payload.get("action", "continue")),
            step=None if payload.get("step") is None else int(payload["step"]),
            before=dict(payload["before"]) if payload.get("before") else None,
            after=dict(payload["after"]) if payload.get("after") else None,
            outcome=payload.get("outcome"),
            applied=bool(payload.get("applied", False)),
            adapter_reason=payload.get("adapter_reason"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": self.action,
            "applied": self.applied,
            "outcome": self.outcome,
        }
        if self.step is not None:
            payload["step"] = self.step
        if self.before is not None:
            payload["before"] = dict(self.before)
        if self.after is not None:
            payload["after"] = dict(self.after)
        if self.adapter_reason is not None:
            payload["adapter_reason"] = self.adapter_reason
        return payload


@dataclass
class TaskOutcome:
    """The host's verdict on the whole task, independent of progress state."""

    success: bool
    verifier: str | None = None
    score: float | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> TaskOutcome:
        if not payload:
            return cls(success=False)
        score = payload.get("score")
        return cls(
            success=bool(payload.get("success", False)),
            verifier=payload.get("verifier"),
            score=None if score is None else float(score),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "verifier": self.verifier,
            "score": self.score,
        }
