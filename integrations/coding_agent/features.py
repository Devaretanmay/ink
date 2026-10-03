from __future__ import annotations

from typing import Any

from .events import AgentEvent

ERROR_TOKENS = (
    "AssertionError",
    "TypeError",
    "ValueError",
    "KeyError",
    "ImportError",
    "SyntaxError",
    "FileNotFoundError",
    "TimeoutError",
    "PermissionError",
)


def normalize_error(error: str | None) -> str:
    if not error:
        return "None"
    cleaned = error.strip()
    for token in ERROR_TOKENS:
        if token in cleaned:
            return token
    first_line = cleaned.splitlines()[0]
    return first_line[:50]


def extract_trajectory_features(events: list[AgentEvent]) -> dict[str, Any]:
    if not events:
        return {
            "same_error_count": 0,
            "failed_command_streak": 0,
            "test_failure_streak": 0,
            "repeated_search_count": 0,
            "repeated_file_read_count": 0,
            "search_query_repeat_count": 0,
            "same_file_edit_count": 0,
            "edit_revert_count": 0,
            "unique_files_read": 0,
            "unique_files_edited": 0,
            "steps_since_new_file": 0,
            "steps_since_new_evidence": 0,
            "steps_since_test_improvement": 0,
            "recent_test_delta": 0,
            "current_error_signature": "None",
        }

    latest_err = "None"
    for ev in reversed(events):
        if ev.error:
            latest_err = normalize_error(ev.error)
            break

    same_error_count = sum(
        1
        for ev in events
        if ev.error and normalize_error(ev.error) == latest_err and latest_err != "None"
    )

    failed_command_streak = 0
    for ev in reversed(events):
        if ev.event_type in ("command_failed", "tool_error"):
            failed_command_streak += 1
        elif ev.event_type in ("command_run", "tool_call", "test_run", "test_passed"):
            break

    test_failure_streak = 0
    for ev in reversed(events):
        if ev.event_type == "test_failed":
            test_failure_streak += 1
        elif ev.event_type == "test_passed":
            break

    seen_searches: dict[str, int] = {}
    repeated_search_count = 0
    for ev in events:
        if ev.event_type == "file_search" and ev.query:
            count = seen_searches.get(ev.query, 0)
            if count > 0:
                repeated_search_count += 1
            seen_searches[ev.query] = count + 1
    search_query_repeat_count = max(seen_searches.values()) if seen_searches else 0

    seen_reads: set[str] = set()
    repeated_file_read_count = 0
    for ev in events:
        if ev.event_type == "file_read" and ev.path:
            if ev.path in seen_reads:
                repeated_file_read_count += 1
            seen_reads.add(ev.path)

    latest_edit_file = None
    for ev in reversed(events):
        if ev.event_type == "file_edit" and ev.path:
            latest_edit_file = ev.path
            break
    same_file_edit_count = (
        sum(1 for ev in events if ev.event_type == "file_edit" and ev.path == latest_edit_file)
        if latest_edit_file
        else 0
    )

    edit_revert_count = sum(1 for ev in events if ev.event_type == "edit_reverted")
    unique_files_read = len(
        {ev.path for ev in events if ev.event_type == "file_read" and ev.path}
    )
    unique_files_edited = len(
        {ev.path for ev in events if ev.event_type == "file_edit" and ev.path}
    )

    observed_files: set[str] = set()
    last_new_file_idx = 0
    for idx, ev in enumerate(events):
        if ev.path and ev.path not in observed_files:
            observed_files.add(ev.path)
            last_new_file_idx = idx
    steps_since_new_file = len(events) - 1 - last_new_file_idx

    last_evidence_idx = 0
    for idx, ev in enumerate(events):
        if ev.event_type in ("file_read", "test_passed") or (
            ev.event_type == "file_search" and ev.details and ev.details.get("match_count", 0) > 0
        ):
            last_evidence_idx = idx
    steps_since_new_evidence = len(events) - 1 - last_evidence_idx

    last_improvement_idx = 0
    test_failed_history: list[int] = []
    for idx, ev in enumerate(events):
        if ev.event_type == "test_passed":
            last_improvement_idx = idx
        elif ev.event_type in ("test_run", "test_failed") and ev.test_counts:
            failed = ev.test_counts.get("failed", 0)
            if test_failed_history and failed < test_failed_history[-1]:
                last_improvement_idx = idx
            test_failed_history.append(failed)
    steps_since_test_improvement = len(events) - 1 - last_improvement_idx

    recent_test_delta = 0
    if len(test_failed_history) >= 2:
        recent_test_delta = test_failed_history[-1] - test_failed_history[-2]

    return {
        "same_error_count": int(same_error_count),
        "failed_command_streak": int(failed_command_streak),
        "test_failure_streak": int(test_failure_streak),
        "repeated_search_count": int(repeated_search_count),
        "repeated_file_read_count": int(repeated_file_read_count),
        "search_query_repeat_count": int(search_query_repeat_count),
        "same_file_edit_count": int(same_file_edit_count),
        "edit_revert_count": int(edit_revert_count),
        "unique_files_read": int(unique_files_read),
        "unique_files_edited": int(unique_files_edited),
        "steps_since_new_file": int(steps_since_new_file),
        "steps_since_new_evidence": int(steps_since_new_evidence),
        "steps_since_test_improvement": int(steps_since_test_improvement),
        "recent_test_delta": int(recent_test_delta),
        "current_error_signature": str(latest_err),
    }
