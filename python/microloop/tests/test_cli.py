from pathlib import Path

import pytest
from microloop.cli import main

FIXTURE = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "sample_trajectory.jsonl"
)


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


def test_monitor_prints_summary(capsys) -> None:
    assert main(["monitor", "--no-color", str(FIXTURE)]) == 0
    output = capsys.readouterr().out
    assert "Microloop" in output
    assert "Stalls" in output
    assert "Steps" in output


def test_replay_json_is_line_delimited(capsys) -> None:
    assert main(["replay", "--json", str(FIXTURE)]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 10
    assert all(line.startswith("{") for line in lines)
