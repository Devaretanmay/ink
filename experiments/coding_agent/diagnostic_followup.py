"""Observe and evaluate the bounded coding.diagnostic_followup candidate site."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from statistics import median
from typing import Any, Literal

from ink import DecisionSite

from .action_dispatch import DispatchObservation

CHOICES = (
    "rerun_with_exit_capture",
    "inspect_command_target",
    "search_error_reference",
    "frontier_fallback",
)

DIAGNOSTIC_FOLLOWUP_SITE = DecisionSite(
    name="coding.diagnostic_followup",
    state_schema={
        "command_family": "string",
        "exit_code": "integer?",
        "failure_class": "string",
        "has_file_reference": "boolean",
        "has_line_reference": "boolean",
        "has_searchable_reference": "boolean",
        "has_missing_dependency": "boolean",
        "retry_count": "integer",
        "stdout_summary": "string",
        "stderr_summary": "string",
    },
    choices=CHOICES,
    fallback_revision="deterministic-diagnostic-followup-v1",
    fallback_model_calls=1,
)

Executability = Literal["FULLY_EXECUTABLE", "PARTIALLY_EXECUTABLE", "GENERATIVE"]

_FILE_TOKEN = re.compile(
    r"(?:(?:<worktree>/)|(?<![\w.]))"
    r"([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.(?:py|js|jsx|ts|tsx|rs|go|toml|json|ya?ml))"
    r"(?::(\d+))?"
)
_MODULE_PATTERNS = (
    re.compile(r"(?:No module named|Cannot find module)\s+['\"]([^'\"]+)['\"]", re.I),
    re.compile(r"cannot import name\s+['\"]([^'\"]+)['\"]", re.I),
    re.compile(r"NameError:\s+name\s+['\"]([^'\"]+)['\"]", re.I),
    re.compile(r"(?:undefined reference to|cannot find symbol)\s+['\"]?([A-Za-z_]\w*)", re.I),
    re.compile(r"ABSOLUTE LOCAL PATH[^:]*:\s+['\"]([^'\"]+)['\"]", re.I),
)
_FAILURE_MARKERS = (
    "error",
    "failed",
    "failure",
    "traceback",
    "not found",
    "no module named",
    "cannot find module",
    "permission denied",
    "deselected",
)
_DIAGNOSTIC_ACTIONS = {
    "rerun_command",
    "inspect_file",
    "search_repo",
    "run_diagnostic",
    "run_test",
}


@dataclass(frozen=True)
class ParameterExtraction:
    value: dict[str, Any] | None
    status: Literal["valid", "absent", "ambiguous", "unsafe"]
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiagnosticObservation:
    task_id: str
    arm: str
    decision_id: str
    state: dict[str, Any]
    observed_choice: str
    executability: Executability
    parameters: dict[str, Any] | None
    extractor_status: str
    valid_actions: tuple[str, ...]
    teacher_action_valid: bool
    teacher_action_useful: bool | None
    final_task_success: bool | None
    frontier_input_tokens: int
    frontier_output_tokens: int
    frontier_reasoning_tokens: int
    frontier_cache_tokens: int
    frontier_turn_latency_ms: float | None
    frontier_cost: float
    source_decision_id: str


def _text(row: DispatchObservation) -> str:
    return f"{row.state['stdout_summary']}\n{row.state['stderr_summary']}"


def _safe_relative_path(value: str) -> str | None:
    value = value.strip().replace("\\", "/")
    if value.startswith("<worktree>/"):
        value = value.removeprefix("<worktree>/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\x00" in value:
        return None
    if len(value) > 500 or not path.suffix:
        return None
    return str(path)


def extract_command_target(row: DispatchObservation) -> ParameterExtraction:
    candidates: set[tuple[str, int | None]] = set()
    for source in (row.previous_command, _text(row)):
        for match in _FILE_TOKEN.finditer(source):
            path = _safe_relative_path(match.group(1))
            if path:
                candidates.add((path, int(match.group(2)) if match.group(2) else None))
    if not candidates:
        return ParameterExtraction(None, "absent")
    if len(candidates) > 1:
        rendered = tuple(sorted(f"{path}:{line}" if line else path for path, line in candidates))
        return ParameterExtraction(None, "ambiguous", rendered)
    path, line = next(iter(candidates))
    value: dict[str, Any] = {"path": path}
    if line is not None:
        value["line"] = line
    return ParameterExtraction(value, "valid", (path,))


def extract_search_reference(row: DispatchObservation) -> ParameterExtraction:
    candidates: set[str] = set()
    for pattern in _MODULE_PATTERNS:
        candidates.update(match.group(1).strip() for match in pattern.finditer(_text(row)))
    valid = {
        value
        for value in candidates
        if 1 < len(value) <= 200 and "\n" not in value and "\x00" not in value
    }
    if not valid:
        return ParameterExtraction(None, "absent")
    if len(valid) > 1:
        return ParameterExtraction(None, "ambiguous", tuple(sorted(valid)))
    query = next(iter(valid))
    return ParameterExtraction({"query": query}, "valid", (query,))


def extract_rerun_command(row: DispatchObservation) -> ParameterExtraction:
    command = row.previous_command.strip()
    if not command:
        return ParameterExtraction(None, "absent")
    if "\x00" in command or "\n" in command or len(command) > 4000:
        return ParameterExtraction(None, "unsafe")
    return ParameterExtraction(
        {"command": command, "wrapper": "capture_exit_v1"}, "valid", (command,)
    )


def _failure_class(row: DispatchObservation) -> str:
    state = row.state
    text = _text(row).lower()
    if state["tests_status"] == "no_tests":
        return "no_tests_selected"
    if state["last_result_class"] != "success":
        return state["last_result_class"]
    if any(marker in text for marker in _FAILURE_MARKERS):
        return "embedded_failure"
    if state["last_exit_code"] not in (0, None):
        return "nonzero_exit"
    return "success"


def is_diagnostic_sequence(row: DispatchObservation) -> bool:
    return _failure_class(row) != "success" and row.observed_next_action in _DIAGNOSTIC_ACTIONS


def _matches_observed(value: str, observed: Any) -> bool:
    if not isinstance(observed, str):
        return False
    return value == observed or value in observed or observed in value


def classify_observation(
    row: DispatchObservation,
    diagnostic_result: DispatchObservation | None = None,
    final_task_success: bool | None = None,
) -> DiagnosticObservation:
    target = extract_command_target(row)
    reference = extract_search_reference(row)
    rerun = extract_rerun_command(row)
    failure_class = _failure_class(row)
    valid_actions: list[str] = []

    # Exit capture is useful only when the exit status is absent/ambiguous, or
    # when the teacher actually selected that diagnostic. Known nonzero status
    # alone does not justify another process execution.
    if rerun.status == "valid" and (
        row.state["last_exit_code"] is None or row.observed_next_action == "rerun_command"
    ):
        valid_actions.append("rerun_with_exit_capture")
    if target.status == "valid":
        valid_actions.append("inspect_command_target")
    if reference.status == "valid":
        valid_actions.append("search_error_reference")

    observed_choice = "frontier_fallback"
    extraction = ParameterExtraction(None, "absent")
    if row.observed_next_action == "rerun_command":
        observed_choice, extraction = "rerun_with_exit_capture", rerun
    elif row.observed_next_action == "inspect_file":
        observed_choice, extraction = "inspect_command_target", target
    elif row.observed_next_action == "search_repo":
        observed_choice, extraction = "search_error_reference", reference

    teacher_valid = (
        observed_choice in valid_actions if observed_choice != "frontier_fallback" else True
    )
    if observed_choice == "frontier_fallback":
        executability: Executability = "GENERATIVE"
    elif extraction.status == "valid":
        executability = "FULLY_EXECUTABLE"
    else:
        executability = "PARTIALLY_EXECUTABLE"

    useful: bool | None = None
    if observed_choice == "rerun_with_exit_capture":
        # OpenCode metadata already supplied these exit codes to Ink. Repeating
        # the command added no new machine-readable evidence in current traces.
        useful = row.state["last_exit_code"] is None and diagnostic_result is not None
    elif observed_choice == "inspect_command_target":
        diagnostic_text = _text(diagnostic_result) if diagnostic_result else ""
        useful = extraction.status == "valid" and bool(diagnostic_text.strip())
    elif observed_choice == "search_error_reference":
        diagnostic_text = _text(diagnostic_result) if diagnostic_result else ""
        useful = (
            extraction.status == "valid"
            and bool(diagnostic_text.strip())
            and "no files found" not in diagnostic_text.lower()
        )

    state = {
        "command_family": row.state["last_command_family"],
        "exit_code": row.state["last_exit_code"],
        "failure_class": failure_class,
        "has_file_reference": target.status == "valid",
        "has_line_reference": bool(target.value and target.value.get("line") is not None),
        "has_searchable_reference": reference.status == "valid",
        "has_missing_dependency": row.state["last_result_class"] == "dependency",
        "retry_count": row.state["retry_count"],
        "stdout_summary": row.state["stdout_summary"],
        "stderr_summary": row.state["stderr_summary"],
    }
    return DiagnosticObservation(
        task_id=row.task_id,
        arm=row.arm,
        decision_id=f"diagnostic:{row.decision_id}",
        state=state,
        observed_choice=observed_choice,
        executability=executability,
        parameters=extraction.value,
        extractor_status=extraction.status,
        valid_actions=tuple(valid_actions) or ("frontier_fallback",),
        teacher_action_valid=teacher_valid,
        teacher_action_useful=useful,
        final_task_success=final_task_success,
        frontier_input_tokens=row.frontier_input_tokens,
        frontier_output_tokens=row.frontier_output_tokens,
        frontier_reasoning_tokens=row.frontier_reasoning_tokens,
        frontier_cache_tokens=row.frontier_cache_tokens,
        frontier_turn_latency_ms=row.frontier_turn_latency_ms,
        frontier_cost=row.frontier_cost,
        source_decision_id=row.decision_id,
    )


def _load_dispatch(path: Path) -> list[DispatchObservation]:
    return [
        DispatchObservation(**json.loads(line))
        for line in path.read_text().splitlines()
        if line
    ]


def _rule_choice(state: dict[str, Any]) -> str:
    """Strongest nonsemantic baseline available from pre-decision fields."""
    if state["has_file_reference"]:
        return "inspect_command_target"
    if state["has_searchable_reference"]:
        return "search_error_reference"
    if state["exit_code"] not in (0, None):
        return "rerun_with_exit_capture"
    return "frontier_fallback"


def summarize(
    rows: list[DiagnosticObservation], *, total_frontier_calls: int
) -> dict[str, Any]:
    fully = [row for row in rows if row.executability == "FULLY_EXECUTABLE"]
    partial = [row for row in rows if row.executability == "PARTIALLY_EXECUTABLE"]
    generative = [row for row in rows if row.executability == "GENERATIVE"]
    fallback = [row for row in rows if row.observed_choice == "frontier_fallback"]
    multi = [row for row in rows if len(row.valid_actions) > 1]
    wrong = [row for row in rows if not row.teacher_action_valid]
    useful = [row for row in fully if row.teacher_action_useful is True]
    latencies = [
        row.frontier_turn_latency_ms
        for row in fully
        if row.frontier_turn_latency_ms is not None
    ]
    class_counts = Counter(row.observed_choice for row in rows)
    majority_choice = class_counts.most_common(1)[0][0] if class_counts else None
    majority_matches = sum(row.observed_choice == majority_choice for row in rows)
    rule_matches = sum(_rule_choice(row.state) == row.observed_choice for row in rows)
    rule_signature: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    for row in rows:
        signature = (
            row.state["failure_class"],
            row.state["has_file_reference"],
            row.state["has_line_reference"],
            row.state["has_searchable_reference"],
            row.state["has_missing_dependency"],
        )
        rule_signature[signature].add(row.observed_choice)
    deterministic_signatures = sum(len(choices) == 1 for choices in rule_signature.values())
    return {
        "schema_version": "1",
        "site": DIAGNOSTIC_FOLLOWUP_SITE.name,
        "total_diagnostic_sequences": len(rows),
        "fully_replaceable": len(fully),
        "partially_replaceable": len(partial),
        "generative": len(generative),
        "fallback": len(fallback),
        "observed_choices": dict(sorted(class_counts.items())),
        "teacher_action_valid": len(rows) - len(wrong),
        "teacher_action_useful": len(useful),
        "final_task_successes": sum(row.final_task_success is True for row in rows),
        "final_task_failures": sum(row.final_task_success is False for row in rows),
        "unambiguous_rate": round((len(rows) - len(multi)) / len(rows), 6) if rows else 0,
        "multi_valid_action_rate": round(len(multi) / len(rows), 6) if rows else 0,
        "clearly_wrong_teacher_action_rate": round(len(wrong) / len(rows), 6) if rows else 0,
        "fallback_rate": round(len(fallback) / len(rows), 6) if rows else 0,
        "total_frontier_calls": total_frontier_calls,
        "whole_agent_max_call_reduction_rate": round(
            len(fully) / total_frontier_calls, 6
        ) if total_frontier_calls else 0,
        "whole_agent_verified_useful_call_reduction_rate": round(
            len(useful) / total_frontier_calls, 6
        ) if total_frontier_calls else 0,
        "fully_replaceable_economics": {
            "input_tokens": sum(row.frontier_input_tokens for row in fully),
            "output_tokens": sum(row.frontier_output_tokens for row in fully),
            "reasoning_tokens": sum(row.frontier_reasoning_tokens for row in fully),
            "cache_tokens": sum(row.frontier_cache_tokens for row in fully),
            "latency_ms": sum(row.frontier_turn_latency_ms or 0 for row in fully),
            "cost": sum(row.frontier_cost for row in fully),
            "median_input_tokens": (
                median(row.frontier_input_tokens for row in fully) if fully else 0
            ),
            "median_output_tokens": (
                median(row.frontier_output_tokens for row in fully) if fully else 0
            ),
            "median_reasoning_tokens": (
                median(row.frontier_reasoning_tokens for row in fully) if fully else 0
            ),
            "median_latency_ms": median(latencies) if latencies else None,
        },
        "rule_baseline": {
            "teacher_agreement_rate": round(rule_matches / len(rows), 6) if rows else 0,
            "majority_choice": majority_choice,
            "majority_teacher_agreement_rate": round(
                majority_matches / len(rows), 6
            ) if rows else 0,
            "signatures": len(rule_signature),
            "single_choice_signatures": deterministic_signatures,
            "single_choice_signature_rate": round(
                deterministic_signatures / len(rule_signature), 6
            ) if rule_signature else 0,
        },
    }


def analyze(
    path: Path,
    *,
    total_frontier_calls: int,
    task_outcomes: dict[tuple[str, str], bool] | None = None,
) -> tuple[list[DiagnosticObservation], dict[str, Any]]:
    dispatch = _load_dispatch(path)
    positions: dict[str, DispatchObservation] = {}
    for index, row in enumerate(dispatch[:-1]):
        following = dispatch[index + 1]
        if (row.arm, row.task_id) == (following.arm, following.task_id):
            positions[row.decision_id] = following
    candidates = [row for row in dispatch if is_diagnostic_sequence(row)]
    observations = [
        classify_observation(
            row,
            positions.get(row.decision_id),
            (task_outcomes or {}).get((row.arm, row.task_id)),
        )
        for row in candidates
    ]
    return observations, summarize(observations, total_frontier_calls=total_frontier_calls)


def load_task_outcomes(paths: list[Path]) -> dict[tuple[str, str], bool]:
    outcomes: dict[tuple[str, str], bool] = {}
    for path in paths:
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if record.get("record_type") != "task":
                continue
            mode = str(record.get("mode", ""))
            task_id = str(record["task_id"])
            success = bool(record["success"])
            outcomes[(mode, task_id)] = success
            outcomes[("ink" if mode != "control" else "control", task_id)] = success
    return outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dispatch_observations", type=Path)
    parser.add_argument("--total-frontier-calls", type=int, required=True)
    parser.add_argument("--telemetry", action="append", type=Path, default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    rows, summary = analyze(
        args.dispatch_observations,
        total_frontier_calls=args.total_frontier_calls,
        task_outcomes=load_task_outcomes(args.telemetry),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "diagnostic_followup_observations.jsonl").write_text(
        "".join(json.dumps(asdict(row), sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    (args.output_dir / "diagnostic_followup_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
