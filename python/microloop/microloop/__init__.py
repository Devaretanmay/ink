"""
Microloop — Keep agents making progress.

A local reliability runtime for autonomous agents. It watches an agent's
execution trajectory, detects when the agent is looping, stalled, regressing,
or operating on stale state, and reports a configured intervention.

The public API is intentionally small: ``Monitor``, ``Policy``, ``Event``,
``Decision`` and ``InterventionAction``. The host owns the agent loop; Microloop
only observes it and returns instructions.

``SCHEMA_VERSION`` is the trajectory schema the ``microloop`` CLI reads. It is
not enforced here: the runtime ``Event`` has no version field and ignores unknown
keys, so ``Monitor.observe`` accepts any payload. Version checking happens at the
file boundary in :mod:`microloop.cli`.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .microloop_core import Monitor as _CoreMonitor
from .microloop_core import Policy as _CorePolicy
from .microloop_core import version as _version

__version__: str = _version()
SCHEMA_VERSION = "0.3.0"

__all__ = [
    "Decision",
    "Event",
    "InterventionAction",
    "Monitor",
    "Policy",
    "ProgressState",
    "SCHEMA_VERSION",
    "__version__",
]


class InterventionAction:
    """What the host is advised to do. Compare with plain strings."""

    Observe = "observe"
    Replan = "replan"
    Stop = "stop"


class ProgressState:
    """Trajectory progress classification."""

    Healthy = "healthy"
    Warning = "warning"
    Stalled = "stalled"
    Regressing = "regressing"


@dataclass
class Event:
    """One agent step."""

    step: int
    action: str
    observation: str
    state: Mapping[str, str] | None = None
    metrics: Mapping[str, float] | None = None
    metadata: Mapping[str, str] | None = None

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
        return payload


@dataclass
class Decision:
    """The runtime's classification of one step."""

    step: int
    status: str
    reasons: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    intervention: str = InterventionAction.Observe
    severity: float = 0.0
    verified_progress: bool = False
    feedback: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Decision:
        return cls(
            step=int(payload["step"]),
            status=str(payload["status"]),
            reasons=[str(reason) for reason in payload.get("reasons", [])],
            evidence=list(payload.get("evidence", [])),
            intervention=str(payload.get("intervention", InterventionAction.Observe)),
            severity=float(payload.get("severity", 0.0)),
            verified_progress=bool(payload.get("verified_progress", False)),
            feedback=payload.get("feedback"),
        )

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
    explicit opt-in.
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
    explicit :class:`Policy`.
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
    ) -> None:
        config = {
            "window": window,
            "repetitions": repetitions,
            "stagnation_steps": stagnation_steps,
            "verification_samples": verification_samples,
            "normalization": normalization,
        }
        policy_json = json.dumps(policy._config) if policy is not None else None
        self._inner = _CoreMonitor(json.dumps(config), policy_json)
        self._step = 0

    def observe(
        self,
        action: str,
        observation: str,
        *,
        state: Mapping[str, str] | None = None,
        metrics: Mapping[str, float] | None = None,
        metadata: Mapping[str, str] | None = None,
        step: int | None = None,
    ) -> Decision:
        """Observe one step and return its :class:`Decision`."""
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
        )
        return Decision.from_dict(
            json.loads(self._inner.observe_json(json.dumps(event.to_dict())))
        )

    def observe_event(self, event: Event) -> Decision:
        """Observe a pre-built :class:`Event` (used by the CLI)."""
        return self.observe(
            action=event.action,
            observation=event.observation,
            state=event.state,
            metrics=event.metrics,
            metadata=event.metadata,
            step=event.step,
        )
