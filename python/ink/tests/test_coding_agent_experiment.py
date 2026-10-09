import json
import os
import subprocess
from pathlib import Path

from experiments.coding_agent.action_dispatch import (
    ACTION_DISPATCH_SITE,
    extract_dispatch_observations,
    summarize,
)
from experiments.coding_agent.core import (
    InkInstrumentor,
    ToolObservation,
    normalize_state,
    verify_tool_result,
)
from experiments.coding_agent.diagnostic_followup import (
    DIAGNOSTIC_FOLLOWUP_SITE,
    classify_observation,
    extract_command_target,
    extract_search_reference,
    is_diagnostic_sequence,
    summarize as summarize_diagnostics,
)
from experiments.coding_agent.opencode import (
    ExperimentConfig,
    IsolatedWorktree,
    TaskSpec,
    comparable_configuration,
    enrich_usage_from_db,
    parse_opencode_events,
    run_experiment,
    session_ids_from_events,
)
from experiments.coding_agent.report import generate_report


def test_state_normalization_removes_volatile_data_and_bounds_text():
    observation = ToolObservation(
        tool_name="Bash",
        command="/usr/bin/python -m pytest tests/test_api.py",
        exit_code=1,
        stderr=(
            "2026-10-08T10:11:12Z /"
            + "Users/alice/project/tests/test_api.py:12: AssertionError\n"
            + "detail\n" * 1000
            + "job 550e8400-e29b-41d4-a716-446655440000 on localhost:43123"
        ),
        retry_count=-2,
    )
    state = normalize_state(observation)
    assert state["command_family"] == "pytest"
    assert "alice" not in state["stderr_summary"]
    assert "550e8400" not in state["stderr_summary"]
    assert "43123" not in state["stderr_summary"]
    assert len(state["stderr_summary"]) <= 2000
    assert state["retry_count"] == 0


def test_verifier_maps_concrete_evidence():
    cases = [
        (ToolObservation("bash", exit_code=0), "success"),
        (ToolObservation("bash", exit_code=1, stderr="AssertionError: expected 2 got 3"), "code_failure"),
        (ToolObservation("bash", exit_code=1, stderr="ModuleNotFoundError: no module named x"), "dependency"),
        (ToolObservation("bash", exit_code=1, stderr="permission denied"), "environment"),
        (ToolObservation("bash", exit_code=2, stderr="strange opaque failure"), "unknown"),
        (ToolObservation("bash", exit_code=None, timed_out=True), "unknown"),
    ]
    assert [verify_tool_result(item).label for item, _ in cases] == [label for _, label in cases]


def test_metric_capture_and_report(tmp_path):
    events = [
        json.dumps(
            {
                "type": "message.updated",
                "properties": {
                    "info": {
                        "id": "m1",
                        "role": "assistant",
                        "cost": 0.25,
                        "tokens": {"input": 100, "output": 20},
                    }
                },
            }
        ),
        json.dumps(
            {
                "type": "message.part.updated",
                "properties": {
                    "part": {
                        "type": "tool",
                        "callID": "c1",
                        "tool": "bash",
                        "state": {
                            "status": "completed",
                            "input": {"command": "pytest -q"},
                            "output": "1 passed",
                            "metadata": {"exitCode": 0},
                            "time": {"start": 1, "end": 2},
                        },
                    }
                },
            }
        ),
    ]
    observations, usage = parse_opencode_events(events)
    assert len(observations) == 1
    assert usage == {
        "frontier_calls": 1,
        "input_tokens": 100,
        "output_tokens": 20,
        "reasoning_tokens": 0,
        "cache_tokens": 0,
        "cost": 0.25,
    }

    telemetry = tmp_path / "telemetry.jsonl"
    telemetry.write_text(
        json.dumps(
            {
                "record_type": "task",
                "mode": "control",
                "success": True,
                "frontier_calls": 1,
                "input_tokens": 100,
                "output_tokens": 20,
                "estimated_model_cost": 0.25,
                "tool_calls": 1,
                "selected_decision_site_calls": 0,
                "ink_exact_serves": 0,
                "ink_learned_serves": 0,
                "ink_fallbacks": 0,
                "comparison_calls": 0,
                "incorrect_ink_decisions": 0,
                "wall_clock_seconds": 2.0,
            }
        )
        + "\n"
    )
    assert generate_report([telemetry])["arms"]["control"]["frontier_calls"] == 1


def test_usage_enrichment_reads_finished_session_from_opencode_db(tmp_path):
    import sqlite3

    db = tmp_path / "opencode.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT)")
    connection.execute(
        "INSERT INTO message VALUES (?,?,?)",
        (
            "msg-1",
            "ses-1",
            json.dumps(
                {
                    "role": "assistant",
                    "tokens": {
                        "input": 100,
                        "output": 20,
                        "reasoning": 5,
                        "cache": {"read": 50, "write": 10},
                    },
                    "cost": 0.01,
                }
            ),
        ),
    )
    connection.execute(
        "INSERT INTO message VALUES (?,?,?)",
        ("msg-2", "ses-1", json.dumps({"role": "user"})),
    )
    connection.commit()
    connection.close()

    lines = ['{"type":"step_start","sessionID":"ses-1"}', "not json"]
    assert session_ids_from_events(lines) == ["ses-1"]
    usage = enrich_usage_from_db(["ses-1"], db)
    assert usage == {
        "frontier_calls": 1,
        "input_tokens": 100,
        "output_tokens": 20,
        "reasoning_tokens": 5,
        "cache_tokens": 60,
        "cost": 0.01,
    }
    assert enrich_usage_from_db(["missing"], db)["frontier_calls"] == 0
    assert enrich_usage_from_db([], db)["frontier_calls"] == 0


def test_action_dispatch_extracts_observed_tool_and_frontier_economics():
    events = [
        json.dumps(
            {
                "type": "tool_use",
                "timestamp": 1000,
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "callID": "call-1",
                    "state": {
                        "status": "completed",
                        "input": {"command": "pytest -q"},
                        "output": "1 failed",
                        "metadata": {"exit": 1},
                    },
                },
            }
        ),
        json.dumps({"type": "step_start", "timestamp": 1200, "part": {"type": "step-start"}}),
        json.dumps(
            {
                "type": "tool_use",
                "timestamp": 1500,
                "part": {
                    "type": "tool",
                    "tool": "read",
                    "callID": "call-2",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "/tmp/worktree/tests/test_api.py"},
                        "output": "assert value == 2",
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "step_finish",
                "timestamp": 1600,
                "part": {
                    "type": "step-finish",
                    "reason": "tool-calls",
                    "tokens": {
                        "input": 100,
                        "output": 20,
                        "reasoning": 5,
                        "cache": {"read": 40, "write": 2},
                    },
                    "cost": 0.01,
                },
            }
        ),
        json.dumps({"type": "step_start", "timestamp": 1700, "part": {"type": "step-start"}}),
        json.dumps(
            {
                "type": "step_finish",
                "timestamp": 1900,
                "part": {
                    "type": "step-finish",
                    "reason": "stop",
                    "tokens": {"input": 50, "output": 10},
                    "cost": 0.005,
                },
            }
        ),
    ]
    rows = extract_dispatch_observations(events, task_id="task", arm="control")
    assert ACTION_DISPATCH_SITE.name == "coding.action_dispatch"
    assert [row.observed_next_action for row in rows] == ["inspect_file", "finish_task"]
    assert rows[0].frontier_turn_latency_ms == 500
    assert rows[0].frontier_input_tokens == 100
    assert rows[0].frontier_cache_tokens == 42
    assert not rows[1].can_skip_frontier
    assert rows[1].next_tool is None


def test_action_dispatch_summary_separates_replaceability_and_ambiguity():
    finish_events = [
        json.dumps(
            {
                "type": "tool_use",
                "timestamp": 1,
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "callID": "c1",
                    "state": {
                        "status": "completed",
                        "input": {"command": "pytest -q"},
                        "output": "1 passed",
                        "metadata": {"exit": 0},
                    },
                },
            }
        ),
        json.dumps({"type": "step_start", "timestamp": 2, "part": {"type": "step-start"}}),
        json.dumps(
            {
                "type": "step_finish",
                "timestamp": 3,
                "part": {"type": "step-finish", "reason": "stop", "tokens": {}},
            }
        ),
    ]
    rows = extract_dispatch_observations(finish_events, task_id="task", arm="control")
    report = summarize(rows, total_frontier_turns=2)
    assert report["observations"] == 1
    assert report["fully_replaceable"] == 0
    assert report["partially_replaceable"] == 1
    assert report["whole_agent_max_turn_reduction_rate"] == 0


def test_diagnostic_followup_extracts_target_without_next_action_leakage():
    events = [
        json.dumps(
            {
                "type": "tool_use",
                "timestamp": 100,
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "callID": "failure",
                    "state": {
                        "status": "completed",
                        "input": {"command": "pytest tests/test_api.py -q"},
                        "output": "1 failed",
                        "metadata": {"exit": 1},
                    },
                },
            }
        ),
        json.dumps({"type": "step_start", "timestamp": 200, "part": {"type": "step-start"}}),
        json.dumps(
            {
                "type": "tool_use",
                "timestamp": 300,
                "part": {
                    "type": "tool",
                    "tool": "read",
                    "callID": "inspect",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "/tmp/worktree/tests/test_api.py"},
                        "output": "assert value == 2",
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "step_finish",
                "timestamp": 350,
                "part": {"type": "step-finish", "reason": "tool-calls", "tokens": {}},
            }
        ),
    ]
    dispatch = extract_dispatch_observations(events, task_id="target", arm="control")[0]
    assert is_diagnostic_sequence(dispatch)
    assert extract_command_target(dispatch).value == {"path": "tests/test_api.py"}
    result = classify_observation(dispatch)
    assert DIAGNOSTIC_FOLLOWUP_SITE.name == "coding.diagnostic_followup"
    assert result.observed_choice == "inspect_command_target"
    assert result.executability == "FULLY_EXECUTABLE"
    assert result.teacher_action_valid


def test_diagnostic_followup_search_and_rerun_use_deterministic_evidence():
    search_events = [
        json.dumps(
            {
                "type": "tool_use",
                "timestamp": 100,
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "callID": "missing",
                    "state": {
                        "status": "completed",
                        "input": {"command": "python3 -c 'import missing_pkg'"},
                        "output": "ModuleNotFoundError: No module named 'missing_pkg'",
                        "metadata": {"exit": 1},
                    },
                },
            }
        ),
        json.dumps({"type": "step_start", "timestamp": 200, "part": {"type": "step-start"}}),
        json.dumps(
            {
                "type": "tool_use",
                "timestamp": 300,
                "part": {
                    "type": "tool",
                    "tool": "grep",
                    "callID": "search",
                    "state": {
                        "status": "completed",
                        "input": {"pattern": "missing_pkg"},
                        "output": "No files found",
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "step_finish",
                "timestamp": 350,
                "part": {"type": "step-finish", "reason": "tool-calls", "tokens": {}},
            }
        ),
    ]
    dispatch = extract_dispatch_observations(search_events, task_id="search", arm="control")[0]
    assert extract_search_reference(dispatch).value == {"query": "missing_pkg"}
    result = classify_observation(dispatch)
    assert result.observed_choice == "search_error_reference"
    assert result.executability == "FULLY_EXECUTABLE"
    report = summarize_diagnostics([result], total_frontier_calls=2)
    assert report["fully_replaceable"] == 1
    assert report["whole_agent_max_call_reduction_rate"] == 0.5


def _git(command: list[str], cwd: Path) -> str:
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _make_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["git", "init", "-q"], repo)
    _git(["git", "config", "user.email", "test@example.com"], repo)
    _git(["git", "config", "user.name", "Test"], repo)
    (repo / "value.txt").write_text("original\n")
    _git(["git", "add", "value.txt"], repo)
    _git(["git", "commit", "-qm", "fixture"], repo)
    return repo, _git(["git", "rev-parse", "HEAD"], repo)


def test_task_isolation_removes_changes_and_preserves_source(tmp_path):
    repo, commit = _make_repo(tmp_path)
    with IsolatedWorktree(repo, commit) as worktree:
        (worktree / "value.txt").write_text("changed\n")
        isolated_path = worktree
    assert (repo / "value.txt").read_text() == "original\n"
    assert not isolated_path.exists()
    assert _git(["git", "worktree", "list", "--porcelain"], repo).count("worktree ") == 1


def test_control_disabled_and_observe_never_serves(tmp_path):
    observation = ToolObservation("bash", command="pytest -q", exit_code=0, stdout="1 passed")
    with InkInstrumentor("control", tmp_path / "control.db") as control:
        assert control.classify(observation, "task") is None
    assert not (tmp_path / "control.db").exists()

    with InkInstrumentor("observe", tmp_path / "observe.db") as observe:
        result = observe.classify(observation, "task")
    assert result["source"] == "fallback"
    assert result["fallback_reason"] == "disabled_kill_switch"
    assert result["decision_id"] is not None


def test_shadow_prediction_never_alters_authoritative_result(tmp_path):
    db = tmp_path / "shadow.db"
    observation = ToolObservation("bash", exit_code=1, stderr="AssertionError: no")
    with InkInstrumentor("observe", db) as observe:
        for index in range(12):
            observe.classify(observation, f"train-{index}")
        observe.client.compile(observe.client._resolve("tool_result.classify"), engine="exact")
    with InkInstrumentor("shadow", db) as shadow:
        result = shadow.classify(observation, "shadow-task")
    assert result["source"] == "fallback"
    assert result["fallback_choice"] == "code_failure"
    assert result["predicted_choice"] == "code_failure"


def test_control_and_observe_run_identical_agent_command_and_patch(tmp_path):
    repo, commit = _make_repo(tmp_path)
    fake = tmp_path / "fake-opencode"
    fake.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "dir=''\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = '--dir' ]; then dir=$2; shift 2; else shift; fi\n"
        "done\n"
        "printf 'agent-change\\n' > \"$dir/value.txt\"\n"
        "printf '%s\\n' '{\"type\":\"message.part.updated\",\"properties\":{\"part\":{\"type\":\"tool\",\"callID\":\"c1\",\"tool\":\"bash\",\"state\":{\"status\":\"completed\",\"input\":{\"command\":\"printf agent-change\"},\"output\":\"ok\",\"metadata\":{\"exitCode\":0},\"time\":{\"start\":1,\"end\":2}}}}}'\n"
    )
    os.chmod(fake, 0o755)
    task = TaskSpec(
        "real-fixture",
        str(repo),
        commit,
        "Change value.txt",
        ("sh", "-c", "test \"$(cat value.txt)\" = agent-change"),
    )
    base = dict(model="provider/model", opencode_executable=str(fake), extra_args=())
    control = ExperimentConfig(
        mode="control", output_dir=tmp_path / "control", ink_db=tmp_path / "control.db", **base
    )
    observe = ExperimentConfig(
        mode="observe", output_dir=tmp_path / "observe", ink_db=tmp_path / "observe.db", **base
    )
    assert comparable_configuration((control, observe))
    control_result = run_experiment(task, control)
    observe_result = run_experiment(task, observe)
    assert control_result["success"] and observe_result["success"]
    assert Path(control_result["patch"]).read_text() == Path(observe_result["patch"]).read_text()
    assert control_result["selected_decision_site_calls"] == 0
    assert observe_result["selected_decision_site_calls"] == 1
    assert control_result["action_dispatch_observations"] == 1
    assert observe_result["action_dispatch_observations"] == 1
    assert (tmp_path / "control" / "action_dispatch.jsonl").exists()
    assert (tmp_path / "observe" / "action_dispatch.jsonl").exists()
