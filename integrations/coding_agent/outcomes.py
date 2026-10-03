from __future__ import annotations

from typing import Any

from .events import AgentEvent
from .features import extract_trajectory_features


def evaluate_progress_resumed(
    before_events: list[AgentEvent],
    after_events: list[AgentEvent],
    window_size: int = 10,
) -> tuple[float | None, str, dict[str, Any]]:
    if not after_events:
        return None, "insufficient_after_events", {"observed_count": 0}

    for ev in after_events:
        if ev.event_type == "test_passed":
            return 1.0, "test_passed", {"event_type": ev.event_type}
        if ev.details and ev.details.get("task_completed"):
            return 1.0, "task_completed", {"event_type": ev.event_type}

    before_feats = extract_trajectory_features(before_events)
    after_feats = extract_trajectory_features(after_events)

    if after_feats["recent_test_delta"] > 0:
        return 0.0, "test_regression", {"delta": after_feats["recent_test_delta"]}
    if after_feats["recent_test_delta"] < 0:
        return 1.0, "test_improvement", {"delta": after_feats["recent_test_delta"]}

    before_err = before_feats["current_error_signature"]
    after_err = after_feats["current_error_signature"]

    if before_err != "None" and after_err == before_err and after_feats["same_error_count"] >= 2:
        return 0.0, "same_error_loop_persists", {
            "error": before_err,
            "count": after_feats["same_error_count"],
        }

    before_files = {ev.path for ev in before_events if ev.path}
    after_new_files = {ev.path for ev in after_events if ev.path and ev.path not in before_files}
    if after_new_files:
        return 1.0, "new_file_explored", {"new_files": list(after_new_files)}

    if (
        before_feats["repeated_search_count"] >= 2
        and after_feats["repeated_search_count"] == 0
        and after_feats["unique_files_edited"] > 0
    ):
        return 1.0, "search_loop_broken_with_edits", {}

    if len(after_events) >= window_size:
        if after_feats["failed_command_streak"] >= 3:
            return 0.0, "failed_command_streak_persists", {
                "streak": after_feats["failed_command_streak"]
            }
        return None, "window_elapsed_without_definitive_signal", {"window": len(after_events)}

    return None, "observing_window", {"observed": len(after_events), "window_size": window_size}
