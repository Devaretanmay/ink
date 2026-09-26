"""
Mini-SWE-Agent v2 Adapter and Microloop Agent Integration.
Wraps execution, generates canonical events, and streams trajectory telemetry.
Microloop operates in observation-only mode for baseline runs.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from microloop import Monitor, Policy
from .config import MiniSWEConfig
from .events import build_canonical_event, hash_text

try:
    from minisweagent.agents.default import DefaultAgent, AgentConfig
    from minisweagent.exceptions import Submitted, LimitsExceeded, TimeExceeded
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

        # Initialize Microloop monitor and policy
        self.monitor = Monitor(run_id=run_id, window=32, repetitions=3)
        self.policy = Policy(
            replan=not observer_mode,
            cooldown_steps=cooldown_steps,
            max_replans=max_replans,
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

        # 2. Map to Rust monitor Event schema
        action_hash = hash_text(command)
        from benchmarks.agents.mini_swe.events import mask_volatile_noise_py
        norm_action_hash = hash_text(mask_volatile_noise_py(command))
        obs_hash = hash_text(stdout + stderr)
        norm_obs_hash = hash_text(mask_volatile_noise_py(stdout + stderr))
        err_fp = f"{error_class}:{action_hash}" if exit_code != 0 and error_class else (
            f"Exit{exit_code}:{action_hash}" if exit_code != 0 else None
        )

        rust_event = {
            "schema_version": 1,
            "run_id": self.run_id,
            "step": self.step_counter,
            "action": {
                "name": "shell",
                "fingerprint": action_hash,
                "normalized_fingerprint": norm_action_hash,
            },
            "observation": {
                "success": exit_code == 0,
                "fingerprint": obs_hash,
                "error_fingerprint": err_fp,
                "normalized_fingerprint": norm_obs_hash,
            },
            "verification": (
                {
                    "scope": event["metrics"]["verification_scope"] or "test",
                    "observation_id": f"obs_{self.step_counter}_{obs_hash[:8]}",
                    "failures": event["metrics"]["tests_failed"],
                }
                if event["metrics"]["tests_failed"] is not None
                else None
            ),
            "state_fingerprint": event["workspace"]["diff_hash"] if event["workspace"]["dirty"] else None,
        }

        # 3. Observe via Microloop
        try:
            decision = self.monitor.observe(rust_event)
            intervention = self.policy.apply(decision)
        except Exception as e:
            decision = {
                "schema_version": 1,
                "run_id": self.run_id,
                "step": self.step_counter,
                "state": "healthy",
                "score": 0.0,
                "evidence": [],
                "verified_progress": False,
                "error": str(e),
            }
            intervention = {"kind": "observe", "feedback": None}

        feature_record = {
            "step": self.step_counter,
            "decision": decision,
            "intervention": intervention,
        }
        self.microloop_decisions.append(feature_record)

        return {
            "event": event,
            "decision": decision,
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
