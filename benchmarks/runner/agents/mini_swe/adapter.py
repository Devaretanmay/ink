"""
Mini-SWE-Agent v2 Adapter and Microloop Agent Integration.
Wraps execution, generates canonical events, and streams trajectory telemetry.
Microloop operates in observation-only mode for baseline runs.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from benchmarks.runner.bridge import (
    decision_to_dict,
    intervention_of,
    monitor_for,
    observe_canonical,
)
from .events import build_canonical_event

try:
    from minisweagent.agents.default import DefaultAgent, AgentConfig
    from minisweagent.exceptions import Submitted, LimitsExceeded, TimeExceeded  # noqa: F401
    HAS_MINISWE = True
except ImportError:
    HAS_MINISWE = False
    DefaultAgent = object
    AgentConfig = object


class MiniSWEAdapter:
    """
    Adapter between mini-SWE-agent step execution and Microloop telemetry.
    """

    def __init__(
        self,
        run_id: str,
        task_id: str,
        config: Optional[Any] = None,
        observer_mode: bool = True,
        cooldown_steps: int = 3,
        max_replans: int = 2,
    ) -> None:
        self.run_id = run_id
        self.task_id = task_id
        self.config = config
        self.observer_mode = observer_mode
        self.cooldown_steps = cooldown_steps
        self.max_replans = max_replans
        self.step_counter = 0

        # Initialize the Microloop monitor (recovery disabled in observer mode).
        self.monitor = monitor_for(
            observer_mode=observer_mode,
            cooldown_steps=cooldown_steps,
            max_interventions=max_replans,
        )
        self.trajectory_events: List[Dict[str, Any]] = []
        self.microloop_decisions: List[Dict[str, Any]] = []

    def record_step(
        self,
        command: str,
        exit_code: int,
        stdout: str,
        stderr: str,
        duration_ms: int,
        git_head: str = "HEAD",
        dirty: bool = False,
        changed_files: int = 0,
        diff_content: str = "",
        error_class: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Records an execution step into canonical event format.
        Computes the Microloop decision in observation-only mode.
        """
        self.step_counter += 1

        # 1. Build canonical raw event
        event = build_canonical_event(
            run_id=self.run_id,
            task_id=self.task_id,
            step=self.step_counter,
            action_type="shell",
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            git_head=git_head,
            dirty=dirty,
            changed_files=changed_files,
            diff_content=diff_content,
            error_class=error_class,
        )
        self.trajectory_events.append(event)

        # 2. Observe via Microloop (observation-only in observer mode).
        decision = observe_canonical(self.monitor, event)
        intervention = intervention_of(decision)
        decision_dict = decision_to_dict(decision)

        self.microloop_decisions.append(
            {
                "step": self.step_counter,
                "decision": decision_dict,
                "intervention": intervention,
            }
        )

        return {
            "event": event,
            "decision": decision_dict,
            "intervention": intervention,
        }


if HAS_MINISWE:
    class MicroloopSWEAgent(DefaultAgent):
        """
        Specialized Mini-SWE-Agent wrapping execution with real-time Microloop telemetry
        and closed-loop recovery prompt injection.
        """

        def __init__(self, *args, microloop_adapter: MiniSWEAdapter, **kwargs):
            super().__init__(*args, **kwargs)
            self.microloop_adapter = microloop_adapter

        def execute_actions(self, message: dict) -> list[dict]:
            actions = message.get("extra", {}).get("actions", [])
            outputs = []
            for action in actions:
                cmd = action.get("command", "") if isinstance(action, dict) else str(action)
                t_start = time.time()
                try:
                    output = self.env.execute(action)
                except Exception as e:
                    output = {"output": str(e), "returncode": -1, "exception_info": str(e)}

                dur_ms = int((time.time() - t_start) * 1000)
                out_str = output.get("output", "") if isinstance(output, dict) else str(output)
                ret_code = output.get("returncode", 0) if isinstance(output, dict) else 0

                # Capture step into Microloop adapter
                step_res = self.microloop_adapter.record_step(
                    command=cmd,
                    exit_code=ret_code,
                    stdout=out_str,
                    stderr="",
                    duration_ms=dur_ms,
                )
                intervention = step_res.get("intervention", {})

                # Active recovery injection when running in Treatment Condition (Microloop Active)
                if not self.microloop_adapter.observer_mode:
                    if intervention.get("kind") == "replan" and intervention.get("feedback"):
                        feedback = intervention["feedback"]
                        injection = f"\n\n[MICROLOOP RECOVERY DIRECTIVE]\n{feedback}"
                        if isinstance(output, dict):
                            output["output"] = output.get("output", "") + injection
                        else:
                            output = {"output": str(output) + injection, "returncode": ret_code}
                    elif intervention.get("kind") == "stop":
                        stop_msg = "\n\n[MICROLOOP DIRECTIVE: TERMINATE RUN]\nMaximum replan budget reached without progress."
                        if isinstance(output, dict):
                            output["output"] = output.get("output", "") + stop_msg
                        else:
                            output = {"output": str(output) + stop_msg, "returncode": ret_code}

                outputs.append(output)

            return self.add_messages(*self.model.format_observation_messages(message, outputs, self.get_template_vars()))
else:
    class MicroloopSWEAgent:
        pass
