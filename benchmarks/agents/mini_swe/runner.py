"""
Mini-SWE-Agent Task Execution Runner.
Coordinates agent step iteration, patch extraction, and trajectory collection.
Supports:
1. Live execution via mini-swe-agent v2 with LiteLLM / OpenAI / Anthropic models
2. Deterministic execution via mini-swe-agent's test models
3. High-fidelity baseline trajectory simulation across difficulty tiers
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, List, Optional

from .adapter import MiniSWEAdapter, MicroloopSWEAgent, HAS_MINISWE
from .config import MiniSWEConfig
from benchmarks.runner.evaluator import Evaluator


class TaskRunResult:
    def __init__(
        self,
        task_id: str,
        run_id: str,
        condition: str,
        events: List[Dict[str, Any]],
        microloop_decisions: List[Dict[str, Any]],
        final_patch: str,
        duration_seconds: float,
        total_steps: int,
        total_tool_calls: int,
        tokens_prompt: int,
        tokens_completion: int,
        success: bool,
        evaluation_result: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.task_id = task_id
        self.run_id = run_id
        self.condition = condition
        self.events = events
        self.microloop_decisions = microloop_decisions
        self.final_patch = final_patch
        self.duration_seconds = duration_seconds
        self.total_steps = total_steps
        self.total_tool_calls = total_tool_calls
        self.tokens_prompt = tokens_prompt
        self.tokens_completion = tokens_completion
        self.success = success
        self.evaluation_result = evaluation_result or {}


def run_single_task(
    task: Dict[str, Any],
    run_id: str,
    condition: str,
    config: MiniSWEConfig,
    dry_run: bool = False,
) -> TaskRunResult:
    """
    Executes a single task with the mini-SWE-agent harness.
    """
    task_id = task.get("task_id", "unknown_task")
    difficulty = task.get("difficulty", "medium")
    adapter = MiniSWEAdapter(run_id=run_id, task_id=task_id, config=config, observer_mode=(condition == "vanilla"))
    start_time = time.time()
    evaluator = Evaluator(use_docker=config.use_container)

    if dry_run or not HAS_MINISWE or config.provider in ("mock", "offline"):
        # Deterministic simulation matching difficulty tiers
        seed = config.seed
        rng = random.Random(hash(task_id) + seed * 100)

        # Baseline difficulty outcome probabilities:
        # Easy: 70% resolved (50% efficient, 20% wasteful), 30% failed-recoverable
        # Medium: 50% resolved (15% efficient, 35% wasteful), 40% failed-recoverable, 10% failed-irrecoverable
        # Hard: 30% resolved (10% efficient, 20% wasteful), 50% failed-recoverable, 20% failed-irrecoverable
        roll = rng.random()

        if difficulty == "easy":
            is_resolved = roll < 0.70
            is_wasteful = 0.50 <= roll < 0.70
            is_irrecoverable = False
        elif difficulty == "medium":
            is_resolved = roll < 0.50
            is_wasteful = 0.15 <= roll < 0.50
            is_irrecoverable = roll >= 0.90
        else: # hard
            is_resolved = roll < 0.30
            is_wasteful = 0.10 <= roll < 0.30
            is_irrecoverable = roll >= 0.80

        # Generate trajectory steps
        adapter.record_step(
            command="git status",
            exit_code=0,
            stdout="On branch main\nnothing to commit, working tree clean",
            stderr="",
            duration_ms=45,
            git_head="1a2b3c4d",
            dirty=False,
            changed_files=0,
        )

        initial_fail_count = 1 if difficulty == "easy" else (4 if difficulty == "medium" else 7)
        adapter.record_step(
            command=f"pytest tests/ -q",
            exit_code=1,
            stdout=f"{initial_fail_count} failed, 20 passed in 1.1s\nFAILED tests/test_core.py::test_case",
            stderr="",
            duration_ms=1100,
            git_head="1a2b3c4d",
            dirty=False,
            changed_files=0,
            error_class="AssertionError",
        )

        if is_resolved and not is_wasteful:
            # Successful-efficient: 4-6 steps
            adapter.record_step(
                command=f"grep -rn 'def fix_target' src/",
                exit_code=0,
                stdout="src/core.py:42:def fix_target():",
                stderr="",
                duration_ms=60,
                git_head="1a2b3c4d",
                dirty=False,
                changed_files=0,
            )
            adapter.record_step(
                command="git diff",
                exit_code=0,
                stdout="diff --git a/src/core.py b/src/core.py\n+ # targeted fix",
                stderr="",
                duration_ms=30,
                git_head="1a2b3c4d",
                dirty=True,
                changed_files=1,
                diff_content="+ # targeted fix",
            )
            adapter.record_step(
                command="pytest tests/ -q",
                exit_code=0,
                stdout="21 passed in 1.05s",
                stderr="",
                duration_ms=1050,
                git_head="1a2b3c4d",
                dirty=True,
                changed_files=1,
            )
            final_patch = "diff --git a/src/core.py b/src/core.py\n+ # targeted fix\n"

        elif is_resolved and is_wasteful:
            # Successful-wasteful: 16-20 steps, repeating commands & intermediate failed attempts
            for i in range(1, 5):
                adapter.record_step(
                    command=f"grep -rn 'error_target' src/",
                    exit_code=0,
                    stdout=f"src/core.py:{i*10}: error_target",
                    stderr="",
                    duration_ms=50,
                    git_head="1a2b3c4d",
                    dirty=False,
                    changed_files=0,
                )
            for attempt in range(1, 4):
                adapter.record_step(
                    command=f"edit src/core.py attempt_{attempt}",
                    exit_code=0,
                    stdout=f"modified src/core.py attempt {attempt}",
                    stderr="",
                    duration_ms=120,
                    git_head="1a2b3c4d",
                    dirty=True,
                    changed_files=1,
                    diff_content=f"diff_{attempt}",
                )
                adapter.record_step(
                    command="pytest tests/ -q",
                    exit_code=1,
                    stdout=f"{initial_fail_count} failed\nFAILED tests/test_core.py::test_case",
                    stderr="",
                    duration_ms=800,
                    git_head="1a2b3c4d",
                    dirty=True,
                    changed_files=1,
                    error_class="AssertionError",
                )
            # Finally discovers the fix
            adapter.record_step(
                command="edit src/core.py final_fix",
                exit_code=0,
                stdout="applied final working patch",
                stderr="",
                duration_ms=150,
                git_head="1a2b3c4d",
                dirty=True,
                changed_files=1,
                diff_content="diff_final_correct",
            )
            adapter.record_step(
                command="pytest tests/ -q",
                exit_code=0,
                stdout="21 passed in 1.02s",
                stderr="",
                duration_ms=1020,
                git_head="1a2b3c4d",
                dirty=True,
                changed_files=1,
            )
            final_patch = "diff --git a/src/core.py b/src/core.py\n+ # final working patch\n"

        elif not is_resolved and not is_irrecoverable:
            # Failed-recoverable: agent loops on recurring error and test failure stagnation
            for cycle in range(1, 6):
                adapter.record_step(
                    command=f"edit src/module.py mutation_{cycle}",
                    exit_code=0,
                    stdout=f"updated src/module.py variant {cycle}",
                    stderr="",
                    duration_ms=90,
                    git_head="1a2b3c4d",
                    dirty=True,
                    changed_files=1,
                    diff_content=f"diff_stagnant_{cycle % 2}", # Causes oscillation
                )
                # Test failures count remains stagnant at initial_fail_count
                adapter.record_step(
                    command="pytest tests/ -q",
                    exit_code=1,
                    stdout=f"{initial_fail_count} failed, 20 passed\nFAILED tests/test_core.py::test_recurrent_error",
                    stderr="",
                    duration_ms=950,
                    git_head="1a2b3c4d",
                    dirty=True,
                    changed_files=1,
                    error_class="AssertionError",
                )
            final_patch = "diff --git a/src/module.py b/src/module.py\n+ # incomplete attempt\n"

        else:
            # Failed-irrecoverable: immediate syntax/import breakdown
            adapter.record_step(
                command="python -c 'import broken_dep'",
                exit_code=1,
                stdout="",
                stderr="ModuleNotFoundError: No module named 'broken_dep'",
                duration_ms=30,
                git_head="1a2b3c4d",
                dirty=False,
                changed_files=0,
                error_class="ModuleNotFoundError",
            )
            final_patch = ""

        evaluation_result = evaluator.evaluate_patch(task_id, final_patch)
        evaluation_result["resolved"] = is_resolved
        success = is_resolved
    else:
        # Full live mini-swe-agent execution
        try:
            from minisweagent.environments.local import LocalEnvironment
            from minisweagent.models.litellm_model import LiteLLMModel
            from minisweagent.agents.default import AgentConfig

            env = LocalEnvironment()
            model = LiteLLMModel(model_name=config.model, temperature=config.temperature)
            agent_cfg = AgentConfig(
                system_template="",
                instance_template="",
                step_limit=config.max_steps,
            )
            agent = MicroloopSWEAgent(model, env, microloop_adapter=adapter, config=agent_cfg)
            problem = task.get("description") or f"Resolve issue {task_id}"
            agent.run(problem)
            final_patch = agent.serialize().get("info", {}).get("submission", "")
            evaluation_result = evaluator.evaluate_patch(task_id, final_patch)
            success = evaluation_result["resolved"]
        except Exception as e:
            logging.getLogger("runner").error(f"Live mini-swe execution failed: {e}")
            final_patch = ""
            evaluation_result = {"resolved": False, "error": str(e), "tests_passed": [], "tests_failed": ["run_error"]}
            success = False

    duration = time.time() - start_time

    return TaskRunResult(
        task_id=task_id,
        run_id=run_id,
        condition=condition,
        events=adapter.trajectory_events,
        microloop_decisions=adapter.microloop_decisions,
        final_patch=final_patch,
        duration_seconds=duration,
        total_steps=len(adapter.trajectory_events),
        total_tool_calls=len(adapter.trajectory_events),
        tokens_prompt=1200 * len(adapter.trajectory_events),
        tokens_completion=150 * len(adapter.trajectory_events),
        success=success,
        evaluation_result=evaluation_result,
    )
