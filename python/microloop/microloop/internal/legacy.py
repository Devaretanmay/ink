"""
Microloop: keep agents making progress, and adapt how they run.

A local adaptive-execution runtime for autonomous agents. It watches an agent's
execution trajectory, reports whether the run is advancing, and recommends a
runtime action from the progress and the execution conditions behind it.

The public API is intentionally small: ``Monitor``, ``Event``, ``Decision``,
``Policy``, ``ProgressState`` and ``InterventionAction``, plus the runtime
primitives ``RuntimeState``, ``RuntimeDecision``, ``ProgressSnapshot``,
``RuntimeAction``, ``Budget``, ``Capabilities``, ``RuntimeController`` and
``Episode``. The host owns the agent loop; Microloop only observes it and
returns instructions. Pass 1 recommends ``continue``, ``replan`` and ``stop``
only.

``SCHEMA_VERSION`` is the trajectory schema the ``microloop`` CLI reads. It is
not enforced here: the runtime ``Event`` has no version field and ignores unknown
keys, so ``Monitor.observe`` accepts any payload. Version checking happens at the
file boundary in :mod:`microloop.cli`.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..microloop_core import Monitor as _CoreMonitor
from ..microloop_core import Policy as _CorePolicy
from ..microloop_core import version as _version
from .runtime import (
    ActionOutcome,
    ActionScore,
    AdaptationRecord,
    AdaptationResult,
    AgentRuntimeAdapter,
    ApplyResult,
    Budget,
    Capabilities,
    CapabilityLevel,
    ContextCompactor,
    ControllerTrace,
    Episode,
    MockAdapter,
    ModelTier,
    ProgressSnapshot,
    RecommendationReason,
    RuntimeAction,
    RuntimeAdapter,
    RuntimeController,
    RuntimeDecision,
    RuntimeSession,
    RuntimeState,
    ScoredController,
    Strategy,
    TaskOutcome,
    TieredAdapter,
    Usage,
)

__version__: str = _version()
SCHEMA_VERSION = "0.3.0"

__all__ = [
    "ActionOutcome",
    "ActionScore",
    "AdaptationRecord",
    "AdaptationResult",
    "AgentRuntimeAdapter",
    "ApplyResult",
    "Budget",
    "Capabilities",
    "CapabilityLevel",
    "ContextCompactor",
    "ControllerTrace",
    "Decision",
    "Episode",
    "Event",
    "InterventionAction",
    "MockAdapter",
    "ModelTier",
    "Monitor",
    "Policy",
    "ProgressSnapshot",
    "ProgressState",
    "RecommendationReason",
    "RuntimeAction",
    "RuntimeAdapter",
    "RuntimeController",
    "RuntimeDecision",
    "RuntimeSession",
    "RuntimeState",
    "SCHEMA_VERSION",
    "ScoredController",
    "Strategy",
    "TaskOutcome",
    "TieredAdapter",
    "Usage",
    "__version__",
]


class InterventionAction:
    """What the host is advised to do. Compare with plain strings."""

    Observe = "observe"
    Replan = "replan"
    Stop = "stop"


class ProgressState:
    """Trajectory progress classification.

    ``Progressing`` and ``Uncertain`` are the intended future names for
    ``Healthy`` and ``Warning``; both spellings reference the same value until
    the rename lands.
    """

    Healthy = "healthy"
    Warning = "warning"
    Stalled = "stalled"
    Regressing = "regressing"

    # Compatibility aliases describing trajectory dynamics rather than a health
    # check. They are the same values, so existing comparisons keep working.
    Progressing = "healthy"
    Uncertain = "warning"


@dataclass
class Event:
    """One agent step."""

    step: int
    action: str
    observation: str
    state: Mapping[str, str] | None = None
    metrics: Mapping[str, float] | None = None
    metadata: Mapping[str, str] | None = None
    runtime: RuntimeState | Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "step": self.step,
            "action": self.action,
            "observation": self.observation,
        }
        if self.state is not None:
            payload["state"] = dict(self.state)
        if self.metrics is not None:
            payload["metrics"] = dict(self.metrics)
        if self.metadata is not None:
            payload["metadata"] = dict(self.metadata)
        if self.runtime is not None:
            payload["runtime"] = _runtime_payload(self.runtime)
        return payload

    def capability_level(self) -> int:
        """How much runtime data this step exposes (see ``CapabilityLevel``)."""
        if self.runtime is not None:
            return CapabilityLevel.Runtime
        if self.state is not None or self.metrics is not None or self.metadata is not None:
            return CapabilityLevel.Progress
        return CapabilityLevel.Signals


def _runtime_payload(runtime: RuntimeState | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(runtime, RuntimeState):
        return runtime.to_dict()
    return dict(runtime)


@dataclass
class Decision:
    """The runtime's classification of one step.

    The legacy fields (``status``, ``reasons``, ``intervention``, ...) are kept
    for compatibility. ``progress``, ``runtime`` and ``recommendation`` are the
    forward-facing surface.
    """

    step: int
    status: str
    reasons: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    intervention: str = InterventionAction.Observe
    severity: float = 0.0
    verified_progress: bool = False
    feedback: str | None = None
    progress: ProgressSnapshot | None = None
    runtime: RuntimeState | None = None
    recommendation: RuntimeDecision | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Decision:
        progress = payload.get("progress")
        runtime = payload.get("runtime")
        recommendation = payload.get("recommendation")
        return cls(
            step=int(payload["step"]),
            status=str(payload["status"]),
            reasons=[str(reason) for reason in payload.get("reasons", [])],
            evidence=list(payload.get("evidence", [])),
            intervention=str(payload.get("intervention", InterventionAction.Observe)),
            severity=float(payload.get("severity", 0.0)),
            verified_progress=bool(payload.get("verified_progress", False)),
            feedback=payload.get("feedback"),
            progress=ProgressSnapshot.from_dict(progress) if progress is not None else None,
            runtime=RuntimeState.from_dict(runtime) if runtime is not None else None,
            recommendation=(
                RuntimeDecision.from_dict(recommendation)
                if recommendation is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe representation, including the nested runtime types."""
        return {
            "step": self.step,
            "status": self.status,
            "reasons": list(self.reasons),
            "evidence": list(self.evidence),
            "intervention": self.intervention,
            "severity": self.severity,
            "verified_progress": self.verified_progress,
            "feedback": self.feedback,
            "progress": self.progress.to_dict() if self.progress is not None else None,
            "runtime": self.runtime.to_dict() if self.runtime is not None else None,
            "recommendation": (
                self.recommendation.to_dict() if self.recommendation is not None else None
            ),
        }

    @property
    def should_intervene(self) -> bool:
        """True when the policy advises an action other than ``observe``."""
        return self.intervention != InterventionAction.Observe

    @property
    def recovery_context(self) -> str:
        """Recovery prompt to inject when intervening, or an empty string."""
        return self.feedback or ""


class Policy:
    """
    Recovery policy: maps a progress state to an intervention.

    Defaults to observation-only. Automatic ``replan`` or ``stop`` requires an
    explicit opt-in. Prefer :class:`RuntimeController` for new code; ``Policy``
    remains the stable compatibility surface.
    """

    def __init__(
        self,
        *,
        healthy: str = InterventionAction.Observe,
        warning: str = InterventionAction.Observe,
        stalled: str = InterventionAction.Observe,
        regressing: str = InterventionAction.Observe,
        cooldown_steps: int = 5,
        max_interventions: int = 2,
        stop_at_step: int | None = None,
    ) -> None:
        config: dict[str, Any] = {
            "healthy": healthy,
            "warning": warning,
            "stalled": stalled,
            "regressing": regressing,
            "cooldown_steps": cooldown_steps,
            "max_interventions": max_interventions,
        }
        if stop_at_step is not None:
            config["stop_at_step"] = stop_at_step
        self._config = config
        self._inner = _CorePolicy(json.dumps(config))

    def evaluate(self, decision: Decision) -> str:
        if not isinstance(decision, Decision):
            raise TypeError("Policy.evaluate expects a Decision")
        payload = {
            "step": decision.step,
            "status": decision.status,
            "reasons": decision.reasons,
            "evidence": decision.evidence,
            "intervention": decision.intervention,
            "severity": decision.severity,
            "verified_progress": decision.verified_progress,
        }
        return json.loads(self._inner.evaluate_json(json.dumps(payload)))


class Monitor:
    """
    Trajectory progress monitor.

    Defaults to observation-only. Automatic ``replan`` or ``stop`` requires an
    explicit :class:`Policy` or :class:`RuntimeController`.
    """

    def __init__(
        self,
        *,
        window: int = 32,
        repetitions: int = 3,
        stagnation_steps: int = 8,
        verification_samples: int = 3,
        normalization: bool = True,
        policy: Policy | None = None,
        controller: RuntimeController | None = None,
        capabilities: Capabilities | None = None,
        budget: Budget | None = None,
    ) -> None:
        config = {
            "window": window,
            "repetitions": repetitions,
            "stagnation_steps": stagnation_steps,
            "verification_samples": verification_samples,
            "normalization": normalization,
        }
        controller_json = None
        if controller is not None:
            controller_json = json.dumps(controller.config)
            capabilities = controller.capabilities
            budget = controller.budget
            policy_json = None
        else:
            policy_json = json.dumps(policy._config) if policy is not None else None
        capabilities_json = (
            json.dumps(capabilities.to_dict()) if capabilities is not None else None
        )
        budget_json = json.dumps(budget.to_dict()) if budget is not None else None
        self._inner = _CoreMonitor(
            json.dumps(config),
            policy_json,
            budget_json,
            capabilities_json,
            controller_json,
        )
        self._step = 0
        self.capabilities = capabilities
        self.budget = budget

    def observe(
        self,
        action: str,
        observation: str,
        *,
        state: Mapping[str, str] | None = None,
        metrics: Mapping[str, float] | None = None,
        metadata: Mapping[str, str] | None = None,
        runtime: RuntimeState | Mapping[str, Any] | None = None,
        step: int | None = None,
        available: Iterable[str] | None = None,
    ) -> Decision:
        """Observe one step and return its :class:`Decision`.

        ``available`` lists actuator actions the host can perform right now. It
        gates actuators only: ``continue`` and ``stop`` are never filtered, and
        ``None`` means no per-step restriction beyond the declared capabilities.
        """
        if step is None:
            self._step += 1
            step = self._step
        else:
            self._step = max(self._step, step)
        event = Event(
            step=step,
            action=action,
            observation=observation,
            state=state,
            metrics=metrics,
            metadata=metadata,
            runtime=runtime,
        )
        available_json = (
            json.dumps(sorted(available)) if available is not None else None
        )
        return Decision.from_dict(
            json.loads(
                self._inner.observe_json(json.dumps(event.to_dict()), available_json)
            )
        )

    def observe_event(
        self, event: Event, *, available: Iterable[str] | None = None
    ) -> Decision:
        """Observe a pre-built :class:`Event` (used by the CLI)."""
        return self.observe(
            action=event.action,
            observation=event.observation,
            state=event.state,
            metrics=event.metrics,
            metadata=event.metadata,
            runtime=event.runtime,
            step=event.step,
            available=available,
        )
