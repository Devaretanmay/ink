import json
import re
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


def test_inspect_summarizes_trajectory(capsys) -> None:
    assert main(["inspect", str(FIXTURE)]) == 0
    output = capsys.readouterr().out
    assert "Microloop trajectory analysis" in output
    assert "stalled" in output


def test_inspect_reports_detection_without_an_intervention_policy(capsys) -> None:
    """`inspect` must not surface the replan a host policy would choose."""
    assert main(["inspect", str(FIXTURE)]) == 0
    output = capsys.readouterr().out
    assert "replan" not in output.lower()
    assert "observe" in output


def _fields(output: str) -> dict[str, str]:
    """Parse the fixed-width `label   value` block `inspect` prints."""
    return {
        match.group(1).strip(): match.group(2).strip()
        for match in re.finditer(r"^(\w[\w ]*?)\s{2,}(.*)$", output, re.M)
    }


def test_inspect_reports_the_worst_step_not_the_last(tmp_path, capsys) -> None:
    """A run that loops and then recovers must not summarise as healthy.

    Reporting only the final step made a recovered loop print
    "healthy / none / none" while also naming the step it had detected a
    problem on.
    """
    repeat = {"action": "run tests", "observation": "alpha", "metrics": {"exit_code": 0}}
    path = _trajectory(
        tmp_path,
        {**repeat, "step": 1},
        {**repeat, "step": 2},
        {**repeat, "step": 3},
        {"step": 4, "action": "edit file", "observation": "patched", "metrics": {"exit_code": 0}},
    )
    assert main(["inspect", str(path)]) == 0
    fields = _fields(capsys.readouterr().out)
    assert fields["Status"] == "warning (worst observed)"
    assert fields["Worst at"] == "step 3"
    assert fields["Reasons"] == "repeated_action_result"
    assert fields["Recovered"] == "yes, healthy from step 4"


def test_inspect_reports_not_recovered_when_the_run_ends_unhealthy(tmp_path, capsys) -> None:
    repeat = {"action": "run tests", "observation": "alpha", "metrics": {"exit_code": 0}}
    path = _trajectory(
        tmp_path,
        {**repeat, "step": 1},
        {**repeat, "step": 2},
        {**repeat, "step": 3},
        {**repeat, "step": 4},
    )
    assert main(["inspect", str(path)]) == 0
    assert _fields(capsys.readouterr().out)["Recovered"] == "no"


def test_inspect_on_a_clean_run_reports_no_problem(tmp_path, capsys) -> None:
    path = _trajectory(tmp_path, _step(step=1))
    assert main(["inspect", str(path)]) == 0
    fields = _fields(capsys.readouterr().out)
    assert fields["Status"] == "healthy (worst observed)"
    assert fields["Worst at"] == "n/a"
    assert fields["Recovered"] == "n/a"


def test_inspect_reports_a_regression_that_later_recovers(tmp_path, capsys) -> None:
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
        _step(
            step=6,
            action="pytest",
            observation="0 failed",
            metrics={"exit_code": 0, "failures": 0},
            metadata={"verifier": "pytest", "verification_id": "r6"},
        ),
    )
    assert main(["inspect", str(path)]) == 0
    fields = _fields(capsys.readouterr().out)
    assert fields["Status"] == "regressing (worst observed)"
    assert fields["Worst at"] == "step 5"
    assert fields["Reasons"] == "regression"
    assert fields["Recovered"] == "yes, healthy from step 6"


def test_monitor_prints_summary(capsys) -> None:
    assert main(["monitor", "--no-color", str(FIXTURE)]) == 0
    output = capsys.readouterr().out
    assert "Microloop" in output
    assert "Stalls" in output
    assert "Steps" in output


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
    assert "healthy" in capsys.readouterr().out


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
