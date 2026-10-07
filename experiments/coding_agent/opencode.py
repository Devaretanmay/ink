"""Deterministic OpenCode runner and JSONL event adapter."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .core import InkInstrumentor, Mode, ToolObservation


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    repository: str
    base_commit: str
    prompt: str
    test_command: tuple[str, ...]
    success_exit_codes: tuple[int, ...] = (0,)
    timeout_seconds: int = 1800

    @classmethod
    def from_dict(cls, value: dict[str, Any], manifest_dir: Path) -> TaskSpec:
        repository = Path(value["repository"])
        if not repository.is_absolute():
            repository = (manifest_dir / repository).resolve()
        return cls(
            task_id=value["task_id"],
            repository=str(repository),
            base_commit=value["base_commit"],
            prompt=value["prompt"],
            test_command=tuple(value["test_command"]),
            success_exit_codes=tuple(value.get("success_exit_codes", (0,))),
            timeout_seconds=int(value.get("timeout_seconds", 1800)),
        )


@dataclass(frozen=True)
class ExperimentConfig:
    model: str
    mode: Mode
    output_dir: Path
    ink_db: Path
    opencode_executable: str = "opencode"
    agent: str = "build"
    variant: str | None = None
    extra_args: tuple[str, ...] = ("--auto",)
    environment: dict[str, str] = field(default_factory=dict)


def load_tasks(path: Path | str) -> list[TaskSpec]:
    manifest = Path(path).resolve()
    payload = json.loads(manifest.read_text())
    tasks = payload["tasks"] if isinstance(payload, dict) else payload
    return [TaskSpec.from_dict(task, manifest.parent) for task in tasks]


class IsolatedWorktree(AbstractContextManager[Path]):
    """Create a detached worktree and remove it even after a failed task."""

    def __init__(self, repository: Path | str, commit: str):
        self.repository = Path(repository).resolve()
        self.commit = commit
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "--verify", f"{self.commit}^{{commit}}"],
            check=True,
            capture_output=True,
            text=True,
        )
        self._temporary = tempfile.TemporaryDirectory(prefix="ink-agent-task-")
        self.path = Path(self._temporary.name) / "worktree"
        subprocess.run(
            ["git", "-C", str(self.repository), "worktree", "add", "--detach", str(self.path), self.commit],
            check=True,
            capture_output=True,
            text=True,
        )
        return self.path

    def __exit__(self, *_args: object) -> None:
        if self.path is not None:
            subprocess.run(
                ["git", "-C", str(self.repository), "worktree", "remove", "--force", str(self.path)],
                check=False,
                capture_output=True,
                text=True,
            )
        if self._temporary is not None:
            self._temporary.cleanup()


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


def _metadata_exit_code(metadata: dict[str, Any]) -> int | None:
    for key in ("exit_code", "exitCode", "exit", "code", "statusCode"):
        parsed = _integer(metadata.get(key))
        if parsed is not None:
            return parsed
    return None


def parse_opencode_events(lines: Iterable[str]) -> tuple[list[ToolObservation], dict[str, Any]]:
    """Read current SDK events plus the compact JSON emitted by `opencode run`."""
    observations: list[ToolObservation] = []
    usage = {
        "frontier_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_tokens": 0,
        "cost": 0.0,
    }
    seen_messages: set[str] = set()
    seen_calls: set[str] = set()

    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        event_type = event.get("type")
        properties = event.get("properties", {})
        info = properties.get("info") or event.get("info")
        if event_type in {"message.updated", "message.updated.1"} and isinstance(info, dict):
            if info.get("role") == "assistant" and info.get("id") not in seen_messages:
                tokens = info.get("tokens", {})
                usage["frontier_calls"] += 1
                usage["input_tokens"] += int(tokens.get("input", 0) or 0)
                usage["output_tokens"] += int(tokens.get("output", 0) or 0)
                usage["reasoning_tokens"] += int(tokens.get("reasoning", 0) or 0)
                usage["cache_tokens"] += int(
                    tokens.get("cache", tokens.get("cached", 0)) or 0
                )
                usage["cost"] += float(info.get("cost", 0.0) or 0.0)
                seen_messages.add(info.get("id", f"anonymous-{len(seen_messages)}"))

        part = properties.get("part") or event.get("part")
        if not isinstance(part, dict) and event_type in {"tool_use", "tool_result"}:
            part = event
        if not isinstance(part, dict) or part.get("type") not in {"tool", "tool_use", "tool_result"}:
            continue
        state = part.get("state", {})
        status = state.get("status") or part.get("status")
        if status not in {"completed", "error"}:
            continue
        call_id = str(part.get("callID") or part.get("call_id") or part.get("id") or "")
        if call_id and call_id in seen_calls:
            continue
        if call_id:
            seen_calls.add(call_id)
        tool_name = str(part.get("tool") or part.get("name") or "unknown")
        inputs = state.get("input") or part.get("input") or {}
        command_value = inputs.get("command", "") if isinstance(inputs, dict) else ""
        command = " ".join(map(str, command_value)) if isinstance(command_value, list) else str(command_value)
        metadata = state.get("metadata") or part.get("metadata") or {}
        output = str(state.get("output") or part.get("output") or "")
        error = str(state.get("error") or part.get("error") or "")
        times = state.get("time") or {}
        observations.append(
            ToolObservation(
                tool_name=tool_name,
                command=command,
                exit_code=_metadata_exit_code(metadata) if status == "completed" else 1,
                timed_out="timeout" in error.lower() or "timed out" in error.lower(),
                stdout=output,
                stderr=error,
                call_id=call_id or None,
                started_at=times.get("start"),
                ended_at=times.get("end"),
                metadata=metadata,
            )
        )
    return observations, usage


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def _run_process(command: list[str], cwd: Path, timeout: int, output_path: Path, env: dict[str, str]) -> tuple[int, bool, str]:
    timed_out = False
    stderr = ""
    with output_path.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=output,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            _, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.terminate()
            try:
                _, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                _, stderr = process.communicate()
    return process.returncode, timed_out, stderr


def run_experiment(task: TaskSpec, config: ExperimentConfig) -> dict[str, Any]:
    """Run one real coding task, verify it, then apply the selected Ink mode."""
    executable = shutil.which(config.opencode_executable)
    if executable is None:
        raise FileNotFoundError(f"OpenCode executable not found: {config.opencode_executable}")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    telemetry_path = config.output_dir / "telemetry.jsonl"
    raw_path = config.output_dir / f"{task.task_id}.{config.mode}.opencode.jsonl"
    stderr_path = config.output_dir / f"{task.task_id}.{config.mode}.stderr.log"
    started_wall = datetime.now(UTC)
    started = time.perf_counter()

    command = [
        executable,
        "run",
        task.prompt,
        "--format",
        "json",
        "--model",
        config.model,
        "--agent",
        config.agent,
    ]
    if config.variant:
        command.extend(("--variant", config.variant))
    command.extend(config.extra_args)
    env = os.environ.copy()
    env.update(config.environment)
    env["NO_COLOR"] = "1"

    with IsolatedWorktree(task.repository, task.base_commit) as worktree:
        command.extend(("--dir", str(worktree)))
        agent_exit, timed_out, agent_stderr = _run_process(
            command, worktree, task.timeout_seconds, raw_path, env
        )
        stderr_path.write_text(agent_stderr, encoding="utf-8")
        test_started = time.perf_counter()
        test = subprocess.run(
            list(task.test_command),
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=task.timeout_seconds,
            env=env,
            check=False,
        )
        verifier_ms = (time.perf_counter() - test_started) * 1000
        patch = subprocess.run(
            ["git", "diff", "--binary", "--no-ext-diff"],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        patch_path = config.output_dir / f"{task.task_id}.{config.mode}.patch"
        patch_path.write_text(patch, encoding="utf-8")

    observations, usage = parse_opencode_events(raw_path.read_text(encoding="utf-8").splitlines())
    decisions: list[dict[str, Any]] = []
    with InkInstrumentor(config.mode, config.ink_db) as instrumentor:
        for observation in observations:
            decision = instrumentor.classify(observation, task.task_id)
            if decision is not None:
                decision["arm"] = "control" if config.mode == "control" else "ink"
                decision["mode"] = config.mode
                decisions.append(decision)
                _append_jsonl(telemetry_path, decision)

    ended_wall = datetime.now(UTC)
    incorrect_serves = sum(item["served_correct"] is False for item in decisions)
    task_record = {
        "record_type": "task",
        "task_id": task.task_id,
        "arm": "control" if config.mode == "control" else "ink",
        "mode": config.mode,
        "start_time": started_wall.isoformat(),
        "end_time": ended_wall.isoformat(),
        "success": test.returncode in task.success_exit_codes,
        "agent_exit_code": agent_exit,
        "agent_timed_out": timed_out,
        "model": config.model,
        "agent": config.agent,
        "base_commit": task.base_commit,
        "frontier_calls": usage["frontier_calls"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "reasoning_tokens": usage["reasoning_tokens"],
        "cache_tokens": usage["cache_tokens"],
        "estimated_model_cost": usage["cost"],
        "tool_calls": len(observations),
        "selected_decision_site_calls": len(decisions),
        "ink_exact_serves": sum(item["source"] == "fast_path" and item["engine"] == "exact" for item in decisions),
        "ink_learned_serves": sum(item["source"] == "fast_path" and item["engine"] != "exact" for item in decisions),
        "ink_fallbacks": sum(item["source"] == "fallback" for item in decisions),
        "comparison_calls": sum(item["comparison"] for item in decisions),
        "incorrect_ink_decisions": incorrect_serves,
        "wall_clock_seconds": time.perf_counter() - started,
        "qualification_overhead_seconds": 0.0,
        "verifier_latency_ms": verifier_ms,
        "test_command": list(task.test_command),
        "test_exit_code": test.returncode,
        "test_stdout_tail": test.stdout[-4000:],
        "test_stderr_tail": test.stderr[-4000:],
        "raw_events": str(raw_path),
        "patch": str(patch_path),
    }
    _append_jsonl(telemetry_path, task_record)
    return task_record


def comparable_configuration(configs: Iterable[ExperimentConfig]) -> bool:
    """Check that only mode, output path, and Ink database differ across arms."""
    normalized = []
    for config in configs:
        data = asdict(config)
        for key in ("mode", "output_dir", "ink_db"):
            data.pop(key)
        normalized.append(data)
    return all(item == normalized[0] for item in normalized[1:]) if normalized else True
