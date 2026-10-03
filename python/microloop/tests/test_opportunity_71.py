from __future__ import annotations

from integrations.coding_agent import AgentEvent
from integrations.coding_agent.opportunity import (
    OpportunityDetector,
    evidence_fingerprint,
)


def _phase7_stagnation() -> list[AgentEvent]:
    return [
        AgentEvent("file_search", query="list_files", details={"match_count": 100}),
        AgentEvent(
            "file_read",
            path="src/jinja2/filters.py",
            details={"line_bucket": "0:8", "match_count": 1},
        ),
        AgentEvent("file_search", query="do_indent", details={"match_count": 2}),
        AgentEvent(
            "file_read",
            path="src/jinja2/filters.py",
            details={"line_bucket": "15:17", "match_count": 1},
        ),
        AgentEvent(
            "file_read",
            path="src/jinja2/filters.py",
            details={"line_bucket": "16:18", "match_count": 1},
        ),
        AgentEvent(
            "file_read",
            path="src/jinja2/filters.py",
            details={"line_bucket": "17:18", "match_count": 1},
        ),
        AgentEvent("file_search", query="do_indent", details={"match_count": 2}),
        AgentEvent(
            "file_read",
            path="src/jinja2/filters.py",
            details={"line_bucket": "16:17", "match_count": 1},
        ),
    ]


def test_phase7_trajectory_fires_information_stagnation() -> None:
    det = OpportunityDetector()
    events = _phase7_stagnation()
    for ev in events:
        det.note(ev)
    opp = det.check(events, "task_02")
    assert opp is not None
    assert opp.reason in ("information_stagnation", "search_loop")
    assert opp.signals["same_file_reads"] >= 3
    assert opp.event_index == len(events)
    assert opp.opportunity_id == f"task_02:opp:{len(events)}"


def test_healthy_exploration_does_not_fire() -> None:
    det = OpportunityDetector()
    events = [
        AgentEvent("file_read", path="a.py", details={"line_bucket": "0:1"}),
        AgentEvent("file_read", path="b.py", details={"line_bucket": "0:1"}),
        AgentEvent("file_search", query="alpha", details={"match_count": 3}),
        AgentEvent("file_search", query="beta", details={"match_count": 2}),
        AgentEvent("file_read", path="c.py", details={"line_bucket": "2:4"}),
    ]
    for ev in events:
        det.note(ev)
    assert det.check(events, "t") is None


def test_search_loop_reason() -> None:
    det = OpportunityDetector()
    events = [
        AgentEvent("file_read", path="a.py", details={"line_bucket": "0:1"}),
        AgentEvent("file_read", path="b.py", details={"line_bucket": "0:1"}),
        AgentEvent("file_search", query="do_indent", details={"match_count": 1}),
        AgentEvent("file_read", path="c.py", details={"line_bucket": "0:1"}),
        AgentEvent("file_search", query="Do_Indent ", details={"match_count": 1}),
    ]
    for ev in events:
        det.note(ev)
    opp = det.check(events, "t")
    assert opp is not None
    assert opp.reason == "search_loop"


def test_repeated_error_and_regression_reasons() -> None:
    det = OpportunityDetector()
    errs = [AgentEvent("command_failed", error="ValueError: bad") for _ in range(4)]
    for ev in errs:
        det.note(ev)
    opp = det.check(errs, "t")
    assert opp is not None
    assert opp.reason == "repeated_error"

    det2 = OpportunityDetector()
    reg = [
        AgentEvent("test_failed", test_counts={"passed": 5, "failed": 1}),
        AgentEvent("test_failed", test_counts={"passed": 4, "failed": 2}),
    ]
    for ev in reg:
        det2.note(ev)
    assert det2.check(reg, "t") is None


def test_cooldown_suppresses_duplicates() -> None:
    det = OpportunityDetector(cooldown_actions=6)
    events = _phase7_stagnation()
    for ev in events:
        det.note(ev)
    first = det.check(events, "t")
    assert first is not None
    assert det.check(events, "t") is None
    extended = events + [
        AgentEvent(
            "file_read",
            path="src/jinja2/filters.py",
            details={"line_bucket": "16:17", "match_count": 1},
        )
        for _ in range(12)
    ]
    for ev in extended[len(events) :]:
        det.note(ev)
    second = det.check(extended, "t")
    assert second is None or second.event_index > first.event_index


def test_evidence_fingerprint_distinguishes_new_from_repeat() -> None:
    a = AgentEvent("file_read", path="f.py", details={"line_bucket": "0:1"})
    b = AgentEvent("file_read", path="f.py", details={"line_bucket": "0:1"})
    c = AgentEvent("file_read", path="f.py", details={"line_bucket": "4:6"})
    assert evidence_fingerprint(a) == evidence_fingerprint(b)
    assert evidence_fingerprint(a) != evidence_fingerprint(c)
    s1 = AgentEvent("file_search", query="Do_Indent", details={"match_count": 2})
    s2 = AgentEvent("file_search", query="do_indent ", details={"match_count": 2})
    assert evidence_fingerprint(s1) == evidence_fingerprint(s2)
