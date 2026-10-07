import json
import os
import subprocess
from pathlib import Path

from experiments.coding_agent.core import (
    InkInstrumentor,
    ToolObservation,
    normalize_state,
    verify_tool_result,
)
from experiments.coding_agent.opencode import (
    ExperimentConfig,
    IsolatedWorktree,
    TaskSpec,
    comparable_configuration,
    parse_opencode_events,
    run_experiment,
)
from experiments.coding_agent.report import generate_report


def test_state_normalization_removes_volatile_data_and_bounds_text():
    observation = ToolObservation(
        tool_name="Bash",
        command="/usr/bin/python -m pytest tests/test_api.py",
        exit_code=1,
        stderr=(
            "2026-10-08T10:11:12Z /Users/alice/project/tests/test_api.py:12: AssertionError\n"
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
