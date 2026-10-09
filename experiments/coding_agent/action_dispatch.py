"""Offline feasibility analysis for the observational coding.action_dispatch site.

The analyzer consumes OpenCode JSONL captured by the existing harness. It never
changes agent prompts, tool execution, Ink qualification, or serving behavior.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any

from ink import DecisionSite

from .core import ToolObservation, _command_family, _normalize_text, verify_tool_result

ACTION_CHOICES = (
    "finish_task",
    "inspect_file",
    "search_repo",
    "rerun_command",
    "run_test",
    "run_diagnostic",
    "execute_task_step",
    "cleanup_workspace",
    "unknown",
)

ACTION_DISPATCH_SITE = DecisionSite(
    name="coding.action_dispatch",
    state_schema={
        "last_tool_name": "string",
        "last_command_family": "string",
        "last_result_class": "string",
        "last_exit_code": "integer?",
        "stdout_summary": "string",
        "stderr_summary": "string",
        "retry_count": "integer",
        "tests_status": "string",
    },
    choices=ACTION_CHOICES,
    fallback_revision="observed-opencode-action-v1",
    fallback_model_calls=1,
)

_TEST_PASS = re.compile(r"\b\d+ passed\b", re.I)
_TEST_FAIL = re.compile(r"\b(?:failed|failures?)\b", re.I)
_NO_TESTS = re.compile(r"\b(?:no tests ran|deselected)\b", re.I)
_EXIT_SUFFIX = re.compile(r"\s*;\s*echo\s+[^;]*(?:exit|status)[^;]*$", re.I)
_WORKTREE_PATH = re.compile(r".*?/worktree/(.+)$")


@dataclass(frozen=True)
class DispatchObservation:
    task_id: str
    arm: str
    decision_id: str
    state: dict[str, Any]
    previous_command: str
    observed_next_action: str
    action_parameters_if_deterministic: dict[str, Any] | None
    parameter_needed: bool
    deterministic_parameter_extraction: bool
    can_skip_frontier: bool
    parameter_source: str
    frontier_turn_latency_ms: float | None
    frontier_input_tokens: int
    frontier_output_tokens: int
    frontier_reasoning_tokens: int
    frontier_cache_tokens: int
    frontier_cost: float
    next_tool: str | None
    next_tool_count: int
    next_tool_input: dict[str, Any] | None
    source_file: str


def _events(lines: Iterable[str]) -> list[dict[str, Any]]:
    parsed = []
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(event, dict):
            parsed.append(event)
    return parsed


def _part(event: dict[str, Any]) -> dict[str, Any]:
    properties = event.get("properties", {})
    part = properties.get("part") or event.get("part")
    return part if isinstance(part, dict) else {}


def _tool_event(event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    part = _part(event)
    if event.get("type") not in {"tool_use", "tool_result", "message.part.updated"}:
        return None
    if part.get("type") not in {"tool", "tool_use", "tool_result"}:
        return None
    state = part.get("state", {})
    status = state.get("status") or part.get("status")
    if status not in {"completed", "error"}:
        return None
    return part, state


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


def _exit_code(state: dict[str, Any]) -> int | None:
    metadata = state.get("metadata") or {}
    for key in ("exit_code", "exitCode", "exit", "code", "statusCode"):
        parsed = _integer(metadata.get(key))
        if parsed is not None:
            return parsed
    if state.get("status") == "completed":
        return 0
    if state.get("status") == "error":
        return 1
    return None


def _tool_input(state: dict[str, Any], part: dict[str, Any]) -> dict[str, Any]:
    value = state.get("input") or part.get("input") or {}
    return value if isinstance(value, dict) else {"value": value}


def _command(inputs: dict[str, Any]) -> str:
    value = inputs.get("command", "")
    if isinstance(value, list):
        return " ".join(map(str, value))
    return str(value)


def _relative_path(value: str) -> str:
    matched = _WORKTREE_PATH.match(value)
    return matched.group(1) if matched else value


def _safe_evidence_value(value: Any) -> Any:
    """Remove host-specific paths from persisted evidence, preserving structure."""
    if isinstance(value, dict):
        return {key: _safe_evidence_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_evidence_value(item) for item in value]
    if not isinstance(value, str):
        return value
    relative = _relative_path(value)
    if relative != value:
        return relative
    return _normalize_text(value)


def _tests_status(command_family: str, output: str, exit_code: int | None) -> str:
    if command_family not in {"pytest", "javascript_test", "go_test", "rust_test"}:
        return "not_test"
    if _TEST_PASS.search(output) and exit_code == 0:
        return "passed"
    if _NO_TESTS.search(output):
        return "no_tests"
    if _TEST_FAIL.search(output) or (exit_code is not None and exit_code != 0):
        return "failed"
    return "unknown"


def _cache_total(value: Any) -> int:
    if isinstance(value, dict):
        return int(value.get("read", 0) or 0) + int(value.get("write", 0) or 0)
    return int(value or 0)


def _same_command(current: str, following: str) -> bool:
    current = current.strip()
    following = _EXIT_SUFFIX.sub("", following.strip())
    return bool(current) and current == following


def _classify_action(
    current: ToolObservation,
    next_tool: str | None,
    next_input: dict[str, Any] | None,
) -> tuple[str, dict[str, Any] | None, bool, bool, str]:
    if next_tool is None:
        # In these traces the stop turn also authored the user-facing answer.
        # A category-only prediction cannot reproduce that required payload.
        return "finish_task", None, True, False, "frontier_generated_final_response"

    tool = next_tool.lower()
    inputs = next_input or {}
    next_command = _command(inputs)
    if tool in {"read", "view", "open"}:
        path = str(inputs.get("filePath") or inputs.get("path") or "")
        relative = _relative_path(path)
        evidence = f"{current.command}\n{current.stdout}\n{current.stderr}"
        deterministic = bool(
            relative and (relative in evidence or Path(relative).name in current.command)
        )
        parameters = {"path": relative} if deterministic else None
        return (
            "inspect_file",
            parameters,
            True,
            deterministic,
            "prior_command_or_result" if deterministic else "frontier_selected_path",
        )
    if tool in {"grep", "search", "rg"}:
        query = str(inputs.get("pattern") or inputs.get("query") or "")
        deterministic = bool(
            query and query.lower() in f"{current.stdout}\n{current.stderr}".lower()
        )
        parameters = {"query": query} if deterministic else None
        return (
            "search_repo",
            parameters,
            True,
            deterministic,
            "error_text" if deterministic else "frontier_selected_query",
        )
    if tool in {"bash", "shell", "terminal", "exec", "command"}:
        if _same_command(current.command, next_command):
            return (
                "rerun_command",
                {"command": current.command},
                True,
                True,
                "last_command",
            )
        family = _command_family(next_command, tool)
        if family in {"pytest", "javascript_test", "go_test", "rust_test"}:
            return "run_test", None, True, False, "frontier_generated_command"
        if re.search(r"(?:^|[;&])\s*(?:chmod|rm\b)", next_command):
            return "cleanup_workspace", None, True, False, "frontier_generated_command"
        if current.tool_name.lower() in {"read", "view", "open"}:
            return "execute_task_step", None, True, False, "task_context_and_frontier_plan"
        return "run_diagnostic", None, True, False, "frontier_generated_command"
    return "unknown", None, True, False, "unsupported_next_tool"


def extract_dispatch_observations(
    lines: Iterable[str], *, task_id: str, arm: str, source_file: str = ""
) -> list[DispatchObservation]:
    """Convert each completed tool event into state -> observed next action."""
    events = _events(lines)
    results: list[DispatchObservation] = []
    retries: Counter[tuple[str, str]] = Counter()

    for index, event in enumerate(events):
        parsed = _tool_event(event)
        if parsed is None:
            continue
        part, tool_state = parsed
        tool_name = str(part.get("tool") or part.get("name") or "unknown")
        inputs = _tool_input(tool_state, part)
        command = _command(inputs)
        output = str(tool_state.get("output") or part.get("output") or "")
        error = str(tool_state.get("error") or part.get("error") or "")
        exit_code = _exit_code(tool_state)
        retry_key = (tool_name.lower(), command.strip())
        retry_count = retries[retry_key]
        retries[retry_key] += 1
        times = tool_state.get("time") or {}
        current = ToolObservation(
            tool_name=tool_name,
            command=command,
            exit_code=exit_code,
            timed_out="timeout" in error.lower() or "timed out" in error.lower(),
            stdout=output,
            stderr=error,
            retry_count=retry_count,
            call_id=str(part.get("callID") or part.get("call_id") or part.get("id") or "") or None,
            started_at=times.get("start"),
            ended_at=times.get("end"),
            metadata=tool_state.get("metadata") or {},
        )

        next_tool = None
        next_tool_count = 0
        next_input = None
        next_action_time = None
        next_turn_start = None
        turn_usage: dict[str, Any] = {}
        for following in events[index + 1 :]:
            following_part = _part(following)
            if following.get("type") == "step_start" and next_turn_start is None:
                next_turn_start = following.get("timestamp")
                continue
            if next_turn_start is None:
                # The first step_finish after a tool result closes the turn
                # which selected that tool. Dispatch starts at the next turn.
                continue
            following_tool = _tool_event(following)
            if following_tool is not None:
                next_part, next_state = following_tool
                next_tool_count += 1
                if next_tool is None:
                    next_tool = str(
                        next_part.get("tool") or next_part.get("name") or "unknown"
                    )
                    next_input = _tool_input(next_state, next_part)
                    next_action_time = following.get("timestamp")
            if following.get("type") == "step_finish":
                turn_usage = following_part.get("tokens") or {}
                turn_usage["cost"] = following_part.get("cost", 0.0)
                if next_action_time is None:
                    next_action_time = following.get("timestamp")
                break

        action, parameters, parameter_needed, deterministic, source = _classify_action(
            current, next_tool, next_input
        )
        if next_tool_count > 1:
            action = "unknown"
            parameters = None
            parameter_needed = True
            deterministic = False
            source = "parallel_multi_tool_frontier_turn"
        family = _command_family(command, tool_name)
        verification = verify_tool_result(current)
        state = {
            "last_tool_name": tool_name.lower() or "unknown",
            "last_command_family": family,
            "last_result_class": verification.label,
            "last_exit_code": exit_code,
            "stdout_summary": _normalize_text(output),
            "stderr_summary": _normalize_text(error),
            "retry_count": retry_count,
            "tests_status": _tests_status(family, output + "\n" + error, exit_code),
        }
        latency = None
        result_time = times.get("end") or event.get("timestamp")
        if isinstance(result_time, (int, float)) and isinstance(next_action_time, (int, float)):
            latency = max(0.0, float(next_action_time - result_time))
        results.append(
            DispatchObservation(
                task_id=task_id,
                arm=arm,
                decision_id=current.call_id or f"{task_id}:{index}",
                state=state,
                previous_command=_safe_evidence_value(command),
                observed_next_action=action,
                action_parameters_if_deterministic=_safe_evidence_value(parameters),
                parameter_needed=parameter_needed,
                deterministic_parameter_extraction=deterministic,
                can_skip_frontier=deterministic,
                parameter_source=source,
                frontier_turn_latency_ms=latency,
                frontier_input_tokens=int(turn_usage.get("input", 0) or 0),
                frontier_output_tokens=int(turn_usage.get("output", 0) or 0),
                frontier_reasoning_tokens=int(turn_usage.get("reasoning", 0) or 0),
                frontier_cache_tokens=_cache_total(turn_usage.get("cache", 0)),
                frontier_cost=float(turn_usage.get("cost", 0.0) or 0.0),
                next_tool=next_tool,
                next_tool_count=next_tool_count,
                next_tool_input=_safe_evidence_value(next_input),
                source_file=source_file,
            )
        )
    return results


def _entropy(counts: Counter[str]) -> float:
    total = sum(counts.values())
    if not total:
        return 0.0
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def summarize(
    observations: list[DispatchObservation], *, total_frontier_turns: int
) -> dict[str, Any]:
    actions = Counter(item.observed_next_action for item in observations)
    states: dict[str, list[DispatchObservation]] = defaultdict(list)
    for item in observations:
        states[json.dumps(item.state, sort_keys=True, separators=(",", ":"))].append(item)
    repeated = sum(len(rows) for rows in states.values() if len(rows) > 1)
    ambiguous = [
        rows
        for rows in states.values()
        if len({row.observed_next_action for row in rows}) > 1
    ]
    replaceable = [item for item in observations if item.can_skip_frontier]
    partial = [
        item
        for item in observations
        if item.parameter_needed and not item.deterministic_parameter_extraction
    ]
    latencies = [
        item.frontier_turn_latency_ms
        for item in observations
        if item.frontier_turn_latency_ms is not None
    ]
    task_runs = {(item.arm, item.task_id) for item in observations}
    unique_tasks = {item.task_id for item in observations}

    paired: dict[tuple[str, int], dict[str, str]] = defaultdict(dict)
    positions: Counter[tuple[str, str]] = Counter()
    for item in observations:
        key = (item.arm, item.task_id)
        position = positions[key]
        positions[key] += 1
        paired[(item.task_id, position)][item.arm] = item.observed_next_action
    comparable = [row for row in paired.values() if len(row) == 2]
    agreement = sum(len(set(row.values())) == 1 for row in comparable)

    return {
        "schema_version": "1",
        "site": ACTION_DISPATCH_SITE.name,
        "observations": len(observations),
        "usable_observations": sum(item.observed_next_action != "unknown" for item in observations),
        "unknown_observations": actions["unknown"],
        "task_runs": len(task_runs),
        "unique_task_prompts": len(unique_tasks),
        "average_decisions_per_run": (
            round(len(observations) / len(task_runs), 6) if task_runs else 0
        ),
        "action_classes_observed": len([name for name, count in actions.items() if count]),
        "action_frequency": dict(sorted(actions.items())),
        "action_entropy_bits": round(_entropy(actions), 6),
        "severe_class_imbalance": bool(actions and max(actions.values()) / len(observations) > 0.5),
        "repeated_state_observations": repeated,
        "repeated_state_rate": round(repeated / len(observations), 6) if observations else 0,
        "ambiguous_state_observations": sum(len(rows) for rows in ambiguous),
        "ambiguous_state_rate": round(
            sum(len(rows) for rows in ambiguous) / len(observations), 6
        ) if observations else 0,
        "paired_action_comparisons": len(comparable),
        "paired_teacher_agreement_rate": (
            round(agreement / len(comparable), 6) if comparable else None
        ),
        "parameterized_actions": sum(item.parameter_needed for item in observations),
        "non_parameterized_actions": sum(not item.parameter_needed for item in observations),
        "fully_replaceable": len(replaceable),
        "partially_replaceable": len(partial),
        "not_replaceable": len(observations) - len(replaceable) - len(partial),
        "total_frontier_turns": total_frontier_turns,
        "whole_agent_max_turn_reduction_rate": round(
            len(replaceable) / total_frontier_turns, 6
        ) if total_frontier_turns else 0,
        "frontier_turn_medians": {
            "input_tokens": (
                median(item.frontier_input_tokens for item in observations) if observations else 0
            ),
            "output_tokens": (
                median(item.frontier_output_tokens for item in observations) if observations else 0
            ),
            "reasoning_tokens": (
                median(item.frontier_reasoning_tokens for item in observations)
                if observations
                else 0
            ),
            "latency_ms": median(latencies) if latencies else None,
            "cost": median(item.frontier_cost for item in observations) if observations else 0,
        },
    }


def analyze_directories(
    directories: list[Path],
) -> tuple[list[DispatchObservation], dict[str, Any]]:
    observations: list[DispatchObservation] = []
    total_frontier_turns = 0
    for directory in directories:
        arm = directory.name.removeprefix("ink-exp-")
        telemetry = directory / "telemetry.jsonl"
        task_rows = {}
        if telemetry.exists():
            for line in telemetry.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                if record.get("record_type") == "task":
                    task_rows[record["task_id"]] = record
                    total_frontier_turns += int(record.get("frontier_calls", 0) or 0)
        for path in sorted(directory.glob("*.opencode.jsonl")):
            task_id = path.name.split(f".{arm}.opencode.jsonl", 1)[0]
            observations.extend(
                extract_dispatch_observations(
                    path.read_text(encoding="utf-8").splitlines(),
                    task_id=task_id,
                    arm=arm,
                    source_file=str(path),
                )
            )
    return observations, summarize(observations, total_frontier_turns=total_frontier_turns)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    observations, summary = analyze_directories(args.directories)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    observations_path = args.output_dir / "action_dispatch_observations.jsonl"
    observations_path.write_text(
        "".join(json.dumps(asdict(item), sort_keys=True) + "\n" for item in observations),
        encoding="utf-8",
    )
    (args.output_dir / "action_dispatch_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
