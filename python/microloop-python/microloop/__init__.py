"""
Microloop — Keep agents making progress.
Real-time trajectory failure detection and recovery for autonomous agents.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Union

from .microloop_core import Microloop as _MicroloopCore
from .microloop_core import Monitor as _MonitorCore
from .microloop_core import Policy as _PolicyCore


class Monitor:
    """
    Trajectory Progress Monitor.
    Tracks execution steps locally, computing deterministic progress decisions.
    """

    def __init__(
        self,
        run_id: str,
        window: int = 32,
        repetitions: int = 3,
        stagnation_steps: int = 8,
        verification_samples: int = 3,
        normalize_actions: bool = True,
        config_json: Optional[str] = None,
    ) -> None:
        self.run_id = run_id
        if config_json is not None and config_json.strip():
            self._inner = _MonitorCore(run_id, config_json)
        else:
            config = {
                "window": window,
                "repetitions": repetitions,
                "stagnation_steps": stagnation_steps,
                "verification_samples": verification_samples,
                "normalize_actions": normalize_actions,
            }
            self._inner = _MonitorCore(run_id, json.dumps(config))

    def observe(self, event: Union[Dict[str, Any], str]) -> Dict[str, Any]:
        """
        Observe a new step event and return the trajectory progress decision.
        """
        event_str = json.dumps(event) if isinstance(event, dict) else event
        decision_str = self._inner.observe_json(event_str)
        return json.loads(decision_str)


class Policy:
    """
    Intervention Policy.
    Maps trajectory decisions into actionable host interventions.
    """

    def __init__(
        self,
        replan: bool = True,
        cooldown_steps: int = 8,
        max_replans: int = 2,
        stop_at_step: Optional[int] = None,
        config_json: Optional[str] = None,
    ) -> None:
        if config_json is not None and config_json.strip():
            self._inner = _PolicyCore(config_json)
        else:
            config: Dict[str, Any] = {
                "replan": replan,
                "cooldown_steps": cooldown_steps,
                "max_replans": max_replans,
            }
            if stop_at_step is not None:
                config["stop_at_step"] = stop_at_step
            self._inner = _PolicyCore(json.dumps(config))

    def apply(self, decision: Union[Dict[str, Any], str]) -> Dict[str, Any]:
        """
        Apply policy to a Decision and return the Intervention instruction.
        """
        decision_str = json.dumps(decision) if isinstance(decision, dict) else decision
        intervention_str = self._inner.apply_json(decision_str)
        return json.loads(intervention_str)


class Runtime:
    """
    Combined Microloop Runtime wrapper for agent loops.
    """

    def __init__(
        self,
        run_id: str,
        window: int = 32,
        repetitions: int = 3,
        stagnation_steps: int = 8,
        replan: bool = True,
        max_replans: int = 2,
        cooldown_steps: int = 8,
        stop_at_step: Optional[int] = None,
    ) -> None:
        self.monitor = Monitor(
            run_id=run_id,
            window=window,
            repetitions=repetitions,
            stagnation_steps=stagnation_steps,
        )
        self.policy = Policy(
            replan=replan,
            cooldown_steps=cooldown_steps,
            max_replans=max_replans,
            stop_at_step=stop_at_step,
        )

    def step(self, event: Union[Dict[str, Any], str]) -> Dict[str, Any]:
        decision = self.monitor.observe(event)
        intervention = self.policy.apply(decision)
        return {
            "decision": decision,
            "intervention": intervention,
        }


# Backwards compatibility
Microloop = _MicroloopCore

__all__ = ["Monitor", "Policy", "Runtime", "Microloop"]
