from __future__ import annotations

from pathlib import Path

import pytest

from integrations.coding_agent.live_groq import (
    SafeToolExecutor,
    get_api_key,
    select_model,
)


def test_missing_api_key_raises_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY is not set"):
        get_api_key()


def test_safe_tool_executor_blocks_forbidden_commands(tmp_path: Path) -> None:
    executor = SafeToolExecutor(tmp_path)
    forbidden = [
        "git push origin main",
        "sudo rm -rf /tmp/foo",
        "rm -rf /",
        "cat ~/.ssh/id_rsa",
        "git reset --hard HEAD~1",
    ]
    for cmd in forbidden:
        with pytest.raises(PermissionError, match="Command forbidden by safety boundary"):
            executor.execute("run_command", {"command": cmd})
        with pytest.raises(PermissionError, match="Command forbidden by safety boundary"):
            executor.execute("run_tests", {"test_command": cmd})


def test_safe_tool_executor_refuses_package_installs(tmp_path: Path) -> None:
    executor = SafeToolExecutor(tmp_path)
    for cmd in ["pip install simplejson", "python -m pip install x", "npm install left-pad"]:
        res, ev = executor.execute("run_command", {"command": cmd})
        assert ev.event_type == "command_failed"
        assert "unavailable" in res


def test_safe_tool_executor_file_operations(tmp_path: Path) -> None:
    executor = SafeToolExecutor(tmp_path)
    f = tmp_path / "hello.py"
    f.write_text("def hello():\n    return 'world'\n")

    res, ev = executor.execute("read_file", {"path": "hello.py"})
    assert "1: def hello():" in res
    assert ev.event_type == "file_read"

    res, ev = executor.execute(
        "edit_file",
        {"path": "hello.py", "old_content": "'world'", "new_content": "'universe'"},
    )
    assert "Successfully edited" in res
    assert f.read_text() == "def hello():\n    return 'universe'\n"
    assert ev.event_type == "file_edit"


def test_model_selection() -> None:
    def noop(model: str) -> None:
        return None

    models = ["llama-3.3-70b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
    assert select_model(models, probe=noop) == "qwen/qwen3.8-27b"

    models_no_qwen = ["llama-3.3-70b", "openai/gpt-oss-120b"]
    assert select_model(models_no_qwen, probe=noop) == "openai/gpt-oss-120b"


def test_model_selection_skips_exhausted_quota() -> None:
    def probe(model: str) -> None:
        if model == "qwen/qwen3.8-27b":
            raise RuntimeError("rate_limit_exceeded: tokens per day")

    models = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
    assert select_model(models, probe=probe) == "openai/gpt-oss-120b"
