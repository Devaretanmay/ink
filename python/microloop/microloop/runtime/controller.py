"""Runtime controller: the runtime adaptation gate.

The stateful decision lives in the native controller (the deterministic
ladder, cooldowns, per-run cap, budget and capability/availability gating).
This wrapper is the Pythonic constructor and exposes
:meth:`RuntimeController.decide` for hosts that want to decide independently of
a :class:`microloop.Monitor`.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from ..microloop_core import RuntimeController as _CoreRuntimeController
from .action import RuntimeAction
from .capabilities import Capabilities
from .decision import RuntimeDecision, Strategy
from .state import Budget, RuntimeState

__all__ = ["RuntimeController", "ScoredController"]


def _action(value: str) -> str:
    """Accept the compatibility ``observe`` spelling alongside ``continue``."""
    return RuntimeAction.Continue if value in ("observe", "observe_only") else value


class RuntimeController:
    """Maps progress and runtime state to a runtime recommendation.

    Defaults to observation-only: every progress state maps to ``continue``, so
    the recommendation is ``continue``. Setting ``stalled`` to an actuator such
    as ``replan`` opts into the adaptive ladder, which then walks the cheapest
    rung first: ``compact_context`` when the context is under pressure, then
    ``replan``, then ``escalate_model``. Progressing for ``deescalate_after``
    steps after an escalation de-escalates.
    """

    def __init__(
        self,
        *,
        healthy: str = RuntimeAction.Continue,
        warning: str = RuntimeAction.Continue,
        stalled: str = RuntimeAction.Continue,
        regressing: str = RuntimeAction.Continue,
        cooldown_steps: int = 5,
        max_interventions: int = 2,
        stop_at_step: int | None = None,
        context_compaction_threshold: float = 0.8,
        deescalate_after: int = 3,
        escalate_cooldown: int = 8,
        strategy: str = Strategy.Rule,
        scoring: dict[str, Any] | None = None,
        capabilities: Capabilities | None = None,
        budget: Budget | None = None,
    ) -> None:
        self.capabilities = capabilities or Capabilities()
        self.budget = budget
        config: dict[str, Any] = {
            "strategy": strategy,
            "healthy": _action(healthy),
            "warning": _action(warning),
            "stalled": _action(stalled),
            "regressing": _action(regressing),
            "cooldown_steps": cooldown_steps,
            "max_interventions": max_interventions,
            "context_compaction_threshold": context_compaction_threshold,
            "deescalate_after": deescalate_after,
            "escalate_cooldown": escalate_cooldown,
        }
        if stop_at_step is not None:
            config["stop_at_step"] = stop_at_step
        if scoring is not None:
            config["scoring"] = dict(scoring)
        self._config = config
        budget_json = json.dumps(budget.to_dict()) if budget is not None else None
        self._inner = _CoreRuntimeController(
            json.dumps(config), budget_json, json.dumps(self.capabilities.to_dict())
        )

    @property
    def config(self) -> dict[str, Any]:
        """The controller configuration, for :class:`microloop.Monitor`."""
        return dict(self._config)

    def decide(self, decision: Any, available: Iterable[str] | None = None) -> RuntimeDecision:
        """Return the runtime recommendation for a :class:`microloop.Decision`."""
        runtime = getattr(decision, "runtime", None) or RuntimeState()
        payload = decision.to_dict() if hasattr(decision, "to_dict") else {}
        available_json = json.dumps(sorted(available)) if available is not None else None
        result = self._inner.decide_json(
            json.dumps(payload), json.dumps(runtime.to_dict()), available_json
        )
        return RuntimeDecision.from_dict(json.loads(result))


class ScoredController(RuntimeController):
    """Controller that scores candidates instead of taking the first rung.

    Same gating and decision surface as :class:`RuntimeController`; the
    difference is that every permitted action is scored and the best is chosen,
    with a stability margin and action-exhaustion. Exposed explicitly rather
    than hidden behind a magic switch so the strategy is inspectable.
    """

    def __init__(
        self,
        *,
        min_benefit: float = 0.15,
        replan_horizon: int = 3,
        compact_horizon: int = 2,
        escalate_horizon: int = 3,
        deescalate_horizon: int = 3,
        **kwargs: Any,
    ) -> None:
        scoring = {
            "min_benefit": min_benefit,
            "replan_horizon": replan_horizon,
            "compact_horizon": compact_horizon,
            "escalate_horizon": escalate_horizon,
            "deescalate_horizon": deescalate_horizon,
        }
        super().__init__(strategy=Strategy.Scored, scoring=scoring, **kwargs)

    @property
    def strategy(self) -> str:
        return Strategy.Scored
