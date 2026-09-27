import json
from pathlib import Path

import pytest
from microloop.cli import main

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_trajectory.jsonl"


def _trajectory(tmp_path: Path, *records: dict) -> Path:
    path = tmp_path / "trajectory.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def _step(**overrides: object) -> dict:
    record = {"schema_version": "0.3.0", "step": 1, "action": "a", "observation": "o"}
    record.update(overrides)
    return record


def test_version_flag() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0


def test_doctor_reports_native_runtime(capsys) -> None:
    assert main(["doctor"]) == 0
    assert "native" in capsys.readouterr().out


def test_inspect_tells_the_trajectory_as_a_story(capsys) -> None:
    assert main(["inspect", str(FIXTURE)]) == 0
    output = capsys.readouterr().out
    assert output.startswith("Microloop")
    assert "stall detected" in output
    assert "repeated action" in output
    assert "Trajectory ended" in output


def test_inspect_uses_plain_language_not_enum_names(capsys) -> None:
    """Enum names are for --json; a person reads sentences."""
    assert main(["inspect", str(FIXTURE)]) == 0
    output = capsys.readouterr().out
    for enum_name in (
        "repeated_action_result",
        "repeated_error",
        "state_stagnation",
        "healthy",
        "worst",
        "recovered",
    ):
        assert enum_name not in output


def test_inspect_never_shows_a_recommendation(capsys) -> None:
    """`inspect` reports what happened, not what a host policy would do."""
    assert main(["inspect", str(FIXTURE)]) == 0
    output = capsys.readouterr().out.lower()
    assert "replan" not in output
    assert "intervention" not in output
    assert "policy" not in output


def test_inspect_shows_only_interesting_transitions(tmp_path, capsys) -> None:
    """Steps that are progressing and add nothing are skipped."""
    # genuinely distinct steps: same action, different observations
    path = _trajectory(
        tmp_path,
        *[
            {
                "step": n,
                "action": "read",
                "observation": f"file-{n}.py",
                "metrics": {"exit_code": 0},
            }
            for n in range(1, 7)
        ],
    )
    assert main(["inspect", str(path)]) == 0
    output = capsys.readouterr().out
    assert "No progress issues across 6 steps." in output
    assert "Trajectory ended progressing." in output
    assert "step 3" not in output


def test_inspect_reports_a_stall_and_a_resumption_in_order(tmp_path, capsys) -> None:
    repeat = {"action": "run tests", "observation": "alpha", "metrics": {"exit_code": 0}}
    path = _trajectory(
        tmp_path,
        {**repeat, "step": 1},
        {**repeat, "step": 2},
        {**repeat, "step": 3},
        {"step": 4, "action": "edit", "observation": "patched", "metrics": {"exit_code": 0}},
    )
    assert main(["inspect", str(path)]) == 0
    output = capsys.readouterr().out
    assert "pattern detected" in output
    assert "repeated action 3 times" in output
    assert "Trajectory ended progressing after 4 steps." in output
    # the event precedes the outcome
    assert output.index("step 3") < output.index("Trajectory ended")


def test_inspect_reports_a_regression_with_its_numbers(tmp_path, capsys) -> None:
    def verified(step: int, failures: int) -> dict:
        return _step(
            step=step,
            action="pytest",
            observation=f"{failures} failed",
            metrics={"exit_code": 1, "failures": failures},
            metadata={"verifier": "pytest", "verification_id": f"r{step}"},
        )

    path = _trajectory(
        tmp_path,
        _step(step=1, metrics={"exit_code": 0}),
        verified(2, 3),
        verified(3, 1),
        verified(4, 1),
        verified(5, 4),
        verified(6, 0),
    )
    assert main(["inspect", str(path)]) == 0
    output = capsys.readouterr().out
    assert "regression detected" in output
    assert "verifier got worse" in output
    assert "progress resumed" in output
    assert "verification improved" in output


def test_inspect_verbose_adds_every_step_and_the_policy(tmp_path, capsys) -> None:
    path = _trajectory(tmp_path, _step(step=1), _step(step=2))
    assert main(["inspect", "--verbose", str(path)]) == 0
    output = capsys.readouterr().out
    assert "status=healthy" in output
    assert "observe-only" in output


def test_inspect_json_is_the_exact_representation(capsys) -> None:
    assert main(["inspect", "--json", str(FIXTURE)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == "0.3.0"
    assert report["steps"] == 10
    assert report["final_state"] in {"healthy", "warning", "stalled", "regressing"}
    assert report["policy"] == "observe-only"
    assert len(report["decisions"]) == 10
    first = report["decisions"][0]
    # exact enums and evidence structures survive in the machine view
    assert first["status"] == "healthy"
    assert isinstance(first["reasons"], list)
    assert isinstance(first["severity"], float)


def test_inspect_never_claims_it_caused_a_recovery(tmp_path, capsys) -> None:
    """"Recovered" implies Microloop did something. In observe-only it did not."""
    repeat = {"action": "run tests", "observation": "alpha", "metrics": {"exit_code": 0}}
    path = _trajectory(
        tmp_path,
        {**repeat, "step": 1},
        {**repeat, "step": 2},
        {**repeat, "step": 3},
        {"step": 4, "action": "edit", "observation": "patched", "metrics": {"exit_code": 0}},
    )
    assert main(["inspect", str(path)]) == 0
    assert "recovered" not in capsys.readouterr().out.lower()


def test_monitor_prints_summary(capsys) -> None:
    assert main(["monitor", "--no-color", str(FIXTURE)]) == 0
    output = capsys.readouterr().out
    assert "Microloop" in output
    assert "Stalls" in output
    assert "Steps" in output


def test_every_step_is_evaluated_against_the_whole_run(tmp_path, capsys) -> None:
    """A fresh Monitor per step would reset history and hide every problem.

    Both --json and --verbose build their own monitor, and both must carry it
    across the loop. Regression test for a bug where they did not, and reported
    worst_state=healthy for a run that visibly stalled.
    """
    repeat = {"action": "run tests", "observation": "alpha", "metrics": {"exit_code": 0}}
    path = _trajectory(
        tmp_path,
        *[{**repeat, "step": n} for n in range(1, 6)],
    )

    assert main(["inspect", "--json", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["final_state"] == "warning"
    assert report["worst_state"] == "warning"
    assert report["decisions"][0]["status"] == "healthy"
    assert report["decisions"][-1]["status"] == "warning"
    assert report["decisions"][-1]["reasons"]

    assert main(["inspect", "--verbose", str(path)]) == 0
    verbose = capsys.readouterr().out
    assert "status=healthy" in verbose
    assert "status=warning" in verbose


def test_replay_keeps_recommendations_separate_from_inspect(capsys) -> None:
    assert main(["replay", str(FIXTURE)]) == 0
    assert "replan" in capsys.readouterr().out


def test_replay_does_not_reproduce_agent_execution(capsys) -> None:
    assert main(["replay", "--json", str(FIXTURE)]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 10
    assert all(line.startswith("{") for line in lines)


@pytest.mark.parametrize("command", ["inspect", "replay", "monitor"])
def test_incompatible_major_schema_is_rejected(tmp_path, command) -> None:
    path = _trajectory(tmp_path, _step(schema_version="1.0.0"))
    with pytest.raises(SystemExit) as exit_info:
        main([command, str(path)])
    message = str(exit_info.value)
    assert "incompatible trajectory schema 1.0.0" in message
    assert "0.3.0" in message


def test_unreadable_schema_version_is_rejected(tmp_path) -> None:
    path = _trajectory(tmp_path, _step(schema_version="banana"))
    with pytest.raises(SystemExit) as exit_info:
        main(["inspect", str(path)])
    assert "unreadable schema_version" in str(exit_info.value)


def test_absent_schema_version_is_tolerated(tmp_path, capsys) -> None:
    path = _trajectory(tmp_path, {"step": 1, "action": "a", "observation": "o"})
    assert main(["inspect", str(path)]) == 0
    output = capsys.readouterr().out
    assert "No progress issues across 1 step." in output


def test_malformed_json_reports_the_line(tmp_path) -> None:
    path = tmp_path / "trajectory.jsonl"
    path.write_text('{"step": 1}\nnot json\n', encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        main(["inspect", str(path)])
    assert ":2: invalid JSON" in str(exit_info.value)


def test_empty_trajectory_is_reported(tmp_path) -> None:
    path = tmp_path / "trajectory.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        main(["inspect", str(path)])
    assert "no trajectory events found" in str(exit_info.value)
