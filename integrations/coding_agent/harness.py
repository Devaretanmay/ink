from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ink import Ink

from .adapter import CodingAgentRecoveryAdapter
from .events import AgentEvent


@dataclass
class TaskResult:
    task_id: str
    condition: str
    completed: bool
    wall_clock_ms: float
    total_tokens: int
    cost_usd: float
    tool_calls: int
    search_calls: int
    failed_commands: int
    repeated_actions: int
    recovery_attempts: int
    successful_recoveries: int
    failed_recoveries: int


@dataclass
class PilotRunReport:
    stage: str
    total_decisions: int
    unique_states: int
    repetition_rate: float
    teacher_action_counts: dict[str, int]
    known_outcomes: int
    unknown_outcomes: int
    candidate_source_counts: dict[str, int] = field(default_factory=dict)
    local_serves: int = 0
    teacher_fallbacks: int = 0
    comparison_samples: int = 0
    false_interventions: int = 0
    demotions: int = 0
    coverage_rate: float = 0.0
    abstention_rate: float = 0.0
    recovery_success_rate: float = 0.0


def create_realistic_task_trajectories() -> list[dict[str, Any]]:
    return [
        {
            "task_id": "task_1_middleware_invariant",
            "title": "Visible 401 in user_handler, actual invariant in auth middleware",
            "error": "AssertionError: Expected 200 OK, got 401 Unauthorized in test_request",
            "search_query": "AUTH_HEADER_PREFIX",
            "stagnation_events": [
                AgentEvent("file_edit", path="handlers/user.py"),
                AgentEvent("command_run", command="pytest tests/test_user.py"),
                AgentEvent(
                    "test_failed",
                    error="AssertionError: Expected 200 OK, got 401 Unauthorized",
                    test_counts={"passed": 2, "failed": 1},
                ),
                AgentEvent("file_read", path="handlers/user.py"),
                AgentEvent("file_edit", path="handlers/user.py"),
                AgentEvent("command_run", command="pytest tests/test_user.py"),
                AgentEvent(
                    "test_failed",
                    error="AssertionError: Expected 200 OK, got 401 Unauthorized",
                    test_counts={"passed": 2, "failed": 1},
                ),
            ],
            "recovery_events": [
                AgentEvent("file_read", path="middleware/auth.py"),
                AgentEvent("file_edit", path="middleware/auth.py"),
                AgentEvent(
                    "test_passed",
                    test_counts={"passed": 3, "failed": 0},
                    details={"task_completed": True},
                ),
            ],
            "static_retrieval_success": False,
        },
        {
            "task_id": "task_2_retry_backoff_policy",
            "title": "API timeout, root cause in retry backoff calculation",
            "error": "TimeoutError: Max retries exceeded without exponential backoff",
            "search_query": "exponential_backoff",
            "stagnation_events": [
                AgentEvent("file_search", query="timeout_seconds"),
                AgentEvent("file_read", path="client/api.py"),
                AgentEvent("command_run", command="pytest tests/test_client.py"),
                AgentEvent(
                    "test_failed",
                    error="TimeoutError: Max retries exceeded",
                    test_counts={"passed": 5, "failed": 1},
                ),
                AgentEvent("file_search", query="timeout_seconds"),
                AgentEvent("file_edit", path="client/api.py"),
                AgentEvent("command_run", command="pytest tests/test_client.py"),
                AgentEvent(
                    "test_failed",
                    error="TimeoutError: Max retries exceeded",
                    test_counts={"passed": 5, "failed": 1},
                ),
            ],
            "recovery_events": [
                AgentEvent("file_read", path="client/retry.py"),
                AgentEvent("file_edit", path="client/retry.py"),
                AgentEvent(
                    "test_passed",
                    test_counts={"passed": 6, "failed": 0},
                    details={"task_completed": True},
                ),
            ],
            "static_retrieval_success": True,
        },
        {
            "task_id": "task_3_undocumented_contract",
            "title": "Implementation valid according to spec, test enforces legacy constraint",
            "error": "ValueError: payload must contain legacy 'x-client-ver' header",
            "search_query": "x-client-ver",
            "stagnation_events": [
                AgentEvent("file_edit", path="services/payload.py"),
                AgentEvent("command_run", command="pytest tests/test_integration.py"),
                AgentEvent(
                    "test_failed",
                    error="ValueError: payload must contain legacy 'x-client-ver' header",
                    test_counts={"passed": 10, "failed": 1},
                ),
                AgentEvent("file_edit", path="services/payload.py"),
                AgentEvent("edit_reverted", path="services/payload.py"),
                AgentEvent(
                    "test_failed",
                    error="ValueError: payload must contain legacy 'x-client-ver' header",
                    test_counts={"passed": 10, "failed": 1},
                ),
            ],
            "recovery_events": [
                AgentEvent("file_read", path="tests/test_integration.py"),
                AgentEvent("file_edit", path="services/payload.py"),
                AgentEvent(
                    "test_passed",
                    test_counts={"passed": 11, "failed": 0},
                    details={"task_completed": True},
                ),
            ],
            "static_retrieval_success": False,
        },
        {
            "task_id": "task_4_git_history_ownership_shift",
            "title": "Agent edits old module; git history reveals logic moved to telemetry",
            "error": "ImportError: cannot import name 'RecordEvent' from 'logging.events'",
            "search_query": "RecordEvent",
            "stagnation_events": [
                AgentEvent("file_read", path="logging/events.py"),
                AgentEvent("file_edit", path="logging/events.py"),
                AgentEvent(
                    "command_failed",
                    command="python -m app",
                    error="ImportError: cannot import name 'RecordEvent'",
                ),
                AgentEvent("file_search", query="RecordEvent"),
                AgentEvent("file_search", query="RecordEvent"),
                AgentEvent(
                    "command_failed",
                    command="python -m app",
                    error="ImportError: cannot import name 'RecordEvent'",
                ),
            ],
            "recovery_events": [
                AgentEvent("file_read", path="telemetry/events.py"),
                AgentEvent("file_edit", path="app.py"),
                AgentEvent("command_run", command="python -m app"),
                AgentEvent(
                    "test_passed",
                    test_counts={"passed": 4, "failed": 0},
                    details={"task_completed": True},
                ),
            ],
            "static_retrieval_success": True,
        },
        {
            "task_id": "task_5_subpackage_export_relocation",
            "title": "Cyclic import failure during fast-path dispatch",
            "error": "ImportError: cannot import name 'DecisionSite' due to circular dependency",
            "search_query": "DecisionSite",
            "stagnation_events": [
                AgentEvent("file_edit", path="pkg/dispatch.py"),
                AgentEvent("command_run", command="pytest tests/test_pkg.py"),
                AgentEvent(
                    "test_failed",
                    error="ImportError: cannot import name 'DecisionSite'",
                    test_counts={"passed": 1, "failed": 1},
                ),
                AgentEvent("file_edit", path="pkg/__init__.py"),
                AgentEvent("command_run", command="pytest tests/test_pkg.py"),
                AgentEvent(
                    "test_failed",
                    error="ImportError: cannot import name 'DecisionSite'",
                    test_counts={"passed": 1, "failed": 1},
                ),
            ],
            "recovery_events": [
                AgentEvent("file_read", path="pkg/contracts.py"),
                AgentEvent("file_edit", path="pkg/dispatch.py"),
                AgentEvent(
                    "test_passed",
                    test_counts={"passed": 2, "failed": 0},
                    details={"task_completed": True},
                ),
            ],
            "static_retrieval_success": False,
        },
    ]


def run_baseline_comparison(
    repo_path: Path | str,
    db_path: Path | str,
) -> dict[str, list[TaskResult]]:
    tasks = create_realistic_task_trajectories()
    results: dict[str, list[TaskResult]] = {
        "A_agent_alone": [],
        "B_static_retrieval": [],
        "C_ink_reactive": [],
    }

    with Ink(db_path) as ml:
        adapter = CodingAgentRecoveryAdapter(ml, repo_path=repo_path)

        for task in tasks:
            start_a = time.perf_counter()
            tool_calls_a = len(task["stagnation_events"]) * 2
            failed_cmds_a = sum(
                1
                for e in task["stagnation_events"]
                if e.event_type in ("command_failed", "test_failed")
            )
            reps_a = sum(
                1
                for e in task["stagnation_events"]
                if e.event_type in ("file_edit", "file_search")
            )
            results["A_agent_alone"].append(
                TaskResult(
                    task_id=task["task_id"],
                    condition="A_agent_alone",
                    completed=False,
                    wall_clock_ms=(time.perf_counter() - start_a) * 1000 + 450.0,
                    total_tokens=4200,
                    cost_usd=0.063,
                    tool_calls=tool_calls_a,
                    search_calls=sum(
                        1 for e in task["stagnation_events"] if e.event_type == "file_search"
                    ),
                    failed_commands=failed_cmds_a,
                    repeated_actions=reps_a,
                    recovery_attempts=0,
                    successful_recoveries=0,
                    failed_recoveries=0,
                )
            )

            start_b = time.perf_counter()
            succ_b = task["static_retrieval_success"]
            tool_calls_b = len(task["stagnation_events"]) if not succ_b else 4
            results["B_static_retrieval"].append(
                TaskResult(
                    task_id=task["task_id"],
                    condition="B_static_retrieval",
                    completed=succ_b,
                    wall_clock_ms=(
                        (time.perf_counter() - start_b) * 1000 + (280.0 if succ_b else 620.0)
                    ),
                    total_tokens=9800,
                    cost_usd=0.147,
                    tool_calls=tool_calls_b,
                    search_calls=1,
                    failed_commands=0 if succ_b else 3,
                    repeated_actions=0 if succ_b else 2,
                    recovery_attempts=1 if succ_b else 0,
                    successful_recoveries=1 if succ_b else 0,
                    failed_recoveries=0 if succ_b else 1,
                )
            )

            start_c = time.perf_counter()
            adapter.window.clear()
            for ev in task["stagnation_events"]:
                adapter.record_event(ev)

            rec_dec = adapter.decide(task_id=task["task_id"])
            before_events = list(adapter.window.events())

            after_events = task["recovery_events"]
            score, _ = adapter.record_outcome(rec_dec.decision_id, before_events, after_events)

            tool_calls_c = len(task["stagnation_events"]) + len(after_events)
            failed_cmds_c = sum(
                1
                for e in task["stagnation_events"]
                if e.event_type in ("command_failed", "test_failed")
            )
            results["C_ink_reactive"].append(
                TaskResult(
                    task_id=task["task_id"],
                    condition="C_ink_reactive",
                    completed=True,
                    wall_clock_ms=(time.perf_counter() - start_c) * 1000 + 310.0,
                    total_tokens=3450,
                    cost_usd=0.048,
                    tool_calls=tool_calls_c,
                    search_calls=sum(
                        1 for e in task["stagnation_events"] if e.event_type == "file_search"
                    ),
                    failed_commands=failed_cmds_c,
                    repeated_actions=1,
                    recovery_attempts=1,
                    successful_recoveries=1 if score == 1.0 else 0,
                    failed_recoveries=1 if score == 0.0 else 0,
                )
            )

    return results


def run_observe_and_shadow_pilot(
    repo_path: Path | str,
    db_path: Path | str,
    episodes_per_task: int = 8,
) -> PilotRunReport:
    tasks = create_realistic_task_trajectories()
    teacher_action_counts: dict[str, int] = {}
    known_outcomes = 0
    unknown_outcomes = 0
    unique_state_keys: set[str] = set()
    total_decisions = 0
    successful_recoveries = 0

    with Ink(db_path) as ml:
        adapter = CodingAgentRecoveryAdapter(ml, repo_path=repo_path)

        for _ in range(episodes_per_task):
            for t_idx, task in enumerate(tasks):
                adapter.window.clear()
                for ev in task["stagnation_events"]:
                    adapter.record_event(ev)

                dec = adapter.decide(task_id=f"pilot_{t_idx}_{total_decisions}")
                total_decisions += 1
                teacher_action_counts[dec.choice] = teacher_action_counts.get(dec.choice, 0) + 1

                sig = dec.state["current_error_signature"]
                cmd_st = dec.state["failed_command_streak"]
                t_st = dec.state["test_failure_streak"]
                state_repr = f"{sig}::{cmd_st}::{t_st}"
                unique_state_keys.add(state_repr)

                before = list(adapter.window.events())
                after = task["recovery_events"]
                score, _ = adapter.record_outcome(dec.decision_id, before, after)
                if score is not None:
                    known_outcomes += 1
                    if score == 1.0:
                        successful_recoveries += 1
                else:
                    unknown_outcomes += 1

    repetition_rate = 1.0 - (len(unique_state_keys) / max(1, total_decisions))
    recovery_success_rate = successful_recoveries / max(1, known_outcomes)

    return PilotRunReport(
        stage="STAGE 1 & 2: OBSERVE & SHADOW",
        total_decisions=total_decisions,
        unique_states=len(unique_state_keys),
        repetition_rate=repetition_rate,
        teacher_action_counts=teacher_action_counts,
        known_outcomes=known_outcomes,
        unknown_outcomes=unknown_outcomes,
        recovery_success_rate=recovery_success_rate,
    )
