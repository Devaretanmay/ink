"""Phase 5 external developer experience, observability, and usability tests."""

import logging

import pytest
from ink import Ink
from ink.decision_cli import main as cli_main
from ink.internal.contracts import PromotionRequirements

REQ = PromotionRequirements(
    min_samples=10,
    min_quality=0.8,
    min_confidence=0.7,
    max_degradation=0.15,
    comparison_rate=0.25,
    min_region_samples=3,
    evaluation_window=50,
)


def test_blind_developer_journey_and_omitted_evidence(tmp_path):
    db_path = str(tmp_path / "blind_dev.db")
    with Ink(db_path) as loop:
        res = loop.decide(
            site="customer_support.route",
            state={"tier": "gold", "channel": "chat"},
            choices=["billing", "technical", "account"],
            fallback=lambda: "billing",
        )
        assert res.choice == "billing"
        assert res.source == "fallback"
        assert res.fallback_reason == "observe"
        assert res.decision_id is not None

        # Verify omitted evidence defaults cleanly without ValueError
        outcome = loop.record_outcome(
            res.decision_id,
            quality=1.0,
            verifier="support_review",
            verifier_version="1",
        )
        assert outcome is not None
        assert outcome.quality == 1.0
        assert outcome.evidence == {}

        st = loop.status("customer_support.route")
        assert st["name"] == "customer_support.route"
        assert st["observations"] == 1
        assert st["outcomes"] == 1
        assert st["blocker"] == "insufficient_task_groups"


def test_executable_loc_integration_count():
    # Exact router integration
    code_exact = """
from ink import Ink
loop = Ink(auto_maintenance=True)
def route_exact(ticket):
    res = loop.decide(
        site="support.route",
        state={"tier": ticket["tier"], "dept": ticket["dept"]},
        choices=("billing", "tech"),
        fallback=lambda: "billing",
    )
    if ticket.get("verified"):
        loop.record_outcome(res.decision_id, quality=1.0, verifier="audit", verifier_version="1")
    return res.choice
"""
    lines = [
        line.strip()
        for line in code_exact.strip().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(lines) <= 15, f"Expected <= 15 executable LOC, got {len(lines)}"

    # Semantic router integration
    code_semantic = """
from ink import Ink
loop = Ink(auto_maintenance=True, maintenance_verifier=lambda s, c: (1.0, {"ok": True}))
def route_sem(ticket):
    res = loop.decide(
        site="support.sem",
        state={"text": ticket["text"]},
        choices=("billing", "tech"),
        fallback=lambda: "billing",
    )
    return res.choice
"""
    lines_sem = [
        line.strip()
        for line in code_semantic.strip().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(lines_sem) <= 15, f"Expected <= 15 executable LOC, got {len(lines_sem)}"


def test_syntax_and_subsequent_decisions(tmp_path):
    db_path = str(tmp_path / "syntax.db")
    with Ink(db_path) as loop:
        # First call defines site and schema dynamically
        res1 = loop.decide(
            site="quick.site",
            state={"query": "refund", "amount": 50},
            choices=("approve", "deny"),
            fallback=lambda: "approve",
        )
        assert res1.choice == "approve"

        # Subsequent call does not require choices
        res2 = loop.decide(
            site="quick.site",
            state={"query": "chargeback", "amount": 100},
            fallback=lambda: "deny",
        )
        assert res2.choice == "deny"


def test_standard_python_logging(tmp_path):
    db_path = str(tmp_path / "logging.db")
    records = []

    class TestHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    test_handler = TestHandler()
    logger = logging.getLogger("ink")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(test_handler)

    try:
        with Ink(db_path) as loop:
            res = loop.decide(
                site="log.test",
                state={"tier": "gold"},
                choices=("a", "b"),
                fallback=lambda: "a",
            )
            loop.record_outcome(
                res.decision_id, quality=1.0, verifier="v", verifier_version="1"
            )

        messages = [r.getMessage() for r in records]
        assert any("Ink initialized" in m for m in messages)
        assert any("Decide: site=log.test" in m for m in messages)
        assert any("Recorded outcome: decision=" in m for m in messages)
    finally:
        logger.removeHandler(test_handler)


def test_event_callback_hook_and_resilience(tmp_path):
    db_path = str(tmp_path / "event.db")
    events = []

    def failing_callback(event_type, payload):
        events.append((event_type, payload))
        raise RuntimeError("Observability sink temporarily unavailable")

    with Ink(db_path, on_event=failing_callback) as loop:
        # Failing callback must NOT cause decide or record_outcome to fail
        res = loop.decide(
            site="event.test",
            state={"status": "active"},
            choices=("allow", "block"),
            fallback=lambda: "allow",
        )
        assert res.choice == "allow"
        outcome = loop.record_outcome(
            res.decision_id, quality=1.0, verifier="v", verifier_version="1"
        )
        assert outcome is not None

    event_types = [e[0] for e in events]
    assert "decision" in event_types
    assert "outcome" in event_types


def test_kill_switch_disable_fast_path(tmp_path):
    db_path = str(tmp_path / "killswitch.db")
    with Ink(db_path, disable_fast_path=True) as loop:
        res = loop.decide(
            site="kill.site",
            state={"active": True},
            choices=("yes", "no"),
            fallback=lambda: "yes",
        )
        assert res.choice == "yes"
        assert res.source == "fallback"
        assert res.fallback_reason == "disabled_kill_switch"


def test_kill_switch_env_vars(tmp_path, monkeypatch):
    db_path = str(tmp_path / "env_kill.db")

    # Test INK_DISABLE_FAST_PATH=1
    monkeypatch.setenv("INK_DISABLE_FAST_PATH", "1")
    with Ink(db_path) as loop:
        res = loop.decide(
            site="env.fast_path",
            state={"k": 1},
            choices=("a", "b"),
            fallback=lambda: "a",
        )
        assert res.fallback_reason == "disabled_kill_switch"

    # Test INK_DISABLED=1 (complete bypass)
    monkeypatch.setenv("INK_DISABLED", "1")
    with Ink(db_path) as loop:
        res = loop.decide(
            site="env.disabled",
            state={"k": 1},
            choices=("a", "b"),
            fallback=lambda: "a",
        )
        assert res.choice == "a"
        assert res.fallback_reason == "globally_disabled"
        assert res.recorded is False
        assert res.decision_id is None
        # Record outcome should be no-op
        out = loop.record_outcome("fake_id", quality=1.0, verifier="v", verifier_version="1")
        assert out is None


def test_compact_multi_site_and_max_age_days(tmp_path):
    db_path = str(tmp_path / "compact.db")
    with Ink(db_path) as loop:
        res1 = loop.decide(
            site="site1", state={"x": 1}, choices=("a", "b"), fallback=lambda: "a"
        )
        res2 = loop.decide(
            site="site2", state={"y": 2}, choices=("c", "d"), fallback=lambda: "c"
        )
        assert res1.choice == "a"
        assert res2.choice == "c"

        # Compact single site with max_age_days
        res_single = loop.compact("site1", max_age_days=30)
        assert res_single["site"] == "site1"
        assert res_single["remaining"] == 1

        # Compact all sites with site=None
        res_all = loop.compact(None, max_age_days=30)
        assert res_all["site"] == "all"
        assert res_all["remaining"] == 2


def test_cli_status_and_inspect_ergonomics(tmp_path, capsys):
    db_path = str(tmp_path / "cli.db")
    with Ink(db_path) as loop:
        loop.decide(
            site="orders.route",
            state={"item": "book"},
            choices=("standard", "express"),
            fallback=lambda: "standard",
        )

    # ink status without site should show all sites
    ret = cli_main(["status", "--db", db_path])
    assert ret == 0
    out = capsys.readouterr().out
    assert "orders.route" in out
    assert "observations  1" in out

    # ink inspect without site should raise clean error
    with pytest.raises(SystemExit) as exc:
        cli_main(["inspect", "--db", db_path])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "inspect requires a site name" in err
