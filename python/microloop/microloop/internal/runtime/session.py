"""Runtime session: the actuator loop.

The native controller decides; this session applies. It is opt-in, so the
default Microloop posture stays advisory. The session never performs model
switches or compaction itself — it asks the adapter, which owns those.

Termination is still the host's call: ``stop`` is reported but never executed.
"""
from __future__ import annotations

from typing import Any

from .action import RuntimeAction
from .adapter import RuntimeAdapter
from .episode import Episode

__all__ = ["RuntimeSession"]

#: Actions a session will hand to an adapter. `continue` and `stop` are
#: host-loop control and are never applied by the session.
ACTUATOR_ACTIONS = (
    RuntimeAction.Replan,
    RuntimeAction.EscalateModel,
    RuntimeAction.DeescalateModel,
    RuntimeAction.CompactContext,
)


class RuntimeSession:
    """Observes steps, decides, and applies adaptations through an adapter.

    Example::

        session = RuntimeSession(monitor, adapter)
        for step in agent.steps():
            decision = session.observe(action=step.action, observation=step.result)
            if decision.recommendation.action == RuntimeAction.Stop:
                break
    """

    def __init__(
        self,
        monitor: Any,
        adapter: RuntimeAdapter,
        *,
        episode: Episode | None = None,
    ) -> None:
        self.monitor = monitor
        self.adapter = adapter
        self.episode = episode if episode is not None else Episode()
        self.adaptations = self.episode.adaptations

    def observe(
        self,
        action: str,
        observation: str,
        *,
        state: Any = None,
        metrics: Any = None,
        metadata: Any = None,
        step: int | None = None,
    ):
        """Observe one step, applying any adaptation the controller recommends."""
        runtime = self.adapter.snapshot()
        available = frozenset(
            candidate for candidate in ACTUATOR_ACTIONS if self.adapter.can_apply(candidate)
        )
        decision = self.monitor.observe(
            action=action,
            observation=observation,
            state=state,
            metrics=metrics,
            metadata=metadata,
            runtime=runtime,
            step=step,
            available=available,
        )

        recommendation = decision.recommendation
        applied = False
        adapter_reason = None
        if recommendation is not None and recommendation.action in ACTUATOR_ACTIONS:
            result = self.adapter.apply(recommendation.action)
            applied = bool(result.applied)
            adapter_reason = result.reason

        # Capture the tier as the step started, not as it ended: a new
        # adaptation's `before` is the state it was chosen under, and the
        # previous adaptation's `after` is the state this step opened in.
        tier = None
        current_tier = getattr(self.adapter, "current_tier", None)
        if callable(current_tier):
            tier = current_tier()

        self.episode.record(
            decision, applied=applied, adapter_reason=adapter_reason, tier=tier
        )
        return decision

    def finish(self, outcome: str | None = None) -> None:
        """Close the episode and propagate the outcome to its adaptations."""
        self.episode.finish(outcome)

    def summary(self) -> dict[str, Any]:
        """Episode summary, including cost per successful task."""
        return self.episode.summary()
