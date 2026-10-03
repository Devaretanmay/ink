from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .events import AgentEvent

REASONS = (
    "information_stagnation",
    "repeated_error",
    "search_loop",
    "edit_oscillation",
    "verification_regression",
)

COOLDOWN_ACTIONS = 6
EVAL_WINDOW = 10


@dataclass(frozen=True, slots=True)
class RecoveryOpportunity:
    opportunity_id: str
    task_id: str
    event_index: int
    reason: str
    signals: dict[str, Any]
    window_size: int


def normalize_intent(text: str | None) -> str:
    cleaned = (text or "").lower().strip()
    return re.sub(r"[^a-z0-9]+", " ", cleaned).strip()


def evidence_fingerprint(ev: AgentEvent) -> str:
    if ev.event_type == "file_read":
        bucket = "full"
        if ev.details and "line_bucket" in ev.details:
            bucket = str(ev.details["line_bucket"])
        return f"read|{ev.path}|{bucket}"
    if ev.event_type == "file_search":
        hits = "?"
        if ev.details and "match_count" in ev.details:
            hits = "hit" if ev.details["match_count"] else "miss"
        return f"search|{normalize_intent(ev.query)}|{hits}"
    if ev.event_type in ("command_failed", "command_run", "test_failed", "test_run"):
        err = normalize_intent(ev.error)[:40] if ev.error else "-"
        counts = ""
        if ev.test_counts:
            counts = f":{ev.test_counts.get('passed', '?')}/{ev.test_counts.get('failed', '?')}"
        return f"{ev.event_type}|{normalize_intent(ev.command)}|{err}{counts}"
    if ev.event_type in ("file_edit", "edit_reverted"):
        return f"{ev.event_type}|{ev.path}"
    return f"{ev.event_type}|{ev.path or ev.command or ev.query or ''}"


def _line_bucket(start: int | None, end: int | None) -> str:
    if not start:
        return "full"
    return f"{start // 50}:{(end or start) // 50}"


class OpportunityDetector:
    def __init__(self, cooldown_actions: int = COOLDOWN_ACTIONS) -> None:
        self.cooldown = cooldown_actions
        self._seen: set[str] = set()
        self._last_fire_index = -(10**9)
        self._last_fire_sig = ""

    def note(self, ev: AgentEvent) -> None:
        self._seen.add(evidence_fingerprint(ev))

    def check(self, events: list[AgentEvent], task_id: str) -> RecoveryOpportunity | None:
        if len(events) < 4:
            return None
        idx = len(events)
        if idx - self._last_fire_index < self.cooldown and not self._new_failure(events):
            return None
        signals = self._signals(events)
        reason = self._reason(signals)
        if reason is None:
            return None
        if reason == self._last_fire_sig and idx - self._last_fire_index < self.cooldown * 2:
            return None
        self._last_fire_index = idx
        self._last_fire_sig = reason
        return RecoveryOpportunity(
            opportunity_id=f"{task_id}:opp:{idx}",
            task_id=task_id,
            event_index=idx,
            reason=reason,
            signals=signals,
            window_size=len(events),
        )

    def _new_failure(self, events: list[AgentEvent]) -> bool:
        recent = events[max(0, len(events) - 3) :]
        for ev in recent:
            if ev.event_type in ("command_failed", "test_failed") and ev.error:
                if evidence_fingerprint(ev) not in self._seen:
                    return True
        return False

    def _signals(self, events: list[AgentEvent]) -> dict[str, Any]:
        window = events[-12:]
        reads: dict[str, int] = {}
        buckets: dict[str, int] = {}
        searches: dict[str, int] = {}
        errors: dict[str, int] = {}
        reverts = 0
        same_file_edits = 0
        last_edit: str | None = None
        for ev in reversed(window):
            if ev.event_type == "file_edit" and ev.path and last_edit is None:
                last_edit = ev.path
        for ev in window:
            if ev.event_type == "file_read" and ev.path:
                reads[ev.path] = reads.get(ev.path, 0) + 1
                key = f"{ev.path}|{ev.details.get('line_bucket') if ev.details else None}"
                buckets[key] = buckets.get(key, 0) + 1
            if ev.event_type == "file_search":
                q = normalize_intent(ev.query)
                if q:
                    searches[q] = searches.get(q, 0) + 1
            if ev.error:
                e = normalize_intent(ev.error)[:40]
                errors[e] = errors.get(e, 0) + 1
            if ev.event_type == "edit_reverted":
                reverts += 1
            if ev.event_type == "file_edit" and last_edit and ev.path == last_edit:
                same_file_edits += 1
        actions_since_edit = actions_since_test = actions_since_new_file = len(window)
        for i, ev in enumerate(reversed(window)):
            is_edit = ev.event_type in ("file_edit", "edit_reverted")
            if is_edit and actions_since_edit == len(window):
                actions_since_edit = i
            is_test = ev.event_type in ("test_failed", "test_passed", "test_run")
            if is_test and actions_since_test == len(window):
                actions_since_test = i
        seen_here: set[str] = set()
        for i, ev in enumerate(reversed(window)):
            fp = evidence_fingerprint(ev)
            if fp not in self._seen and fp not in seen_here:
                actions_since_new_file = i if ev.path else actions_since_new_file
                break
            seen_here.add(fp)
        new_evidence = sum(1 for ev in window if evidence_fingerprint(ev) not in self._seen)
        delta = 0
        failed_counts = [
            e.test_counts["failed"] for e in window if e.test_counts and "failed" in e.test_counts
        ]
        if len(failed_counts) >= 2:
            delta = failed_counts[-1] - failed_counts[-2]
        return {
            "same_file_reads": max(reads.values()) if reads else 0,
            "overlapping_reads": max(buckets.values()) if buckets else 0,
            "same_search_intent": max(searches.values()) if searches else 0,
            "same_error_count": max(errors.values()) if errors else 0,
            "edit_reverts": reverts,
            "same_file_edits": same_file_edits,
            "actions_since_edit": actions_since_edit,
            "actions_since_test": actions_since_test,
            "actions_since_new_file": actions_since_new_file,
            "new_evidence_count": new_evidence,
            "recent_test_delta": delta,
        }

    def _reason(self, s: dict[str, Any]) -> str | None:
        if s["recent_test_delta"] > 0:
            return "verification_regression"
        if s["same_error_count"] >= 3:
            return "repeated_error"
        if s["edit_reverts"] >= 1 or s["same_file_edits"] >= 3:
            return "edit_oscillation"
        if s["same_search_intent"] >= 2:
            return "search_loop"
        if s["same_file_reads"] >= 3 or s["overlapping_reads"] >= 3:
            return "information_stagnation"
        no_edit = s["actions_since_edit"] >= 6
        no_test = s["actions_since_test"] >= 6
        if no_edit and no_test and s["new_evidence_count"] == 0:
            return "information_stagnation"
        if s["actions_since_new_file"] >= 6 and s["same_file_reads"] >= 3:
            return "information_stagnation"
        return None
