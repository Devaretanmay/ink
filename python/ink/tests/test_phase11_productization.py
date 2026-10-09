"""Tests for Ink Phase 11 — Productization.

Verifies:
- Public SDK error hierarchy
- DecisionSite metadata and hash preservation
- @ink.wrap decorator (sync and async)
- Outcome ergonomics in record_outcome
- PromotionRequirements defaults and exports
- CLI commands: status, doctor, inspect
- Console API endpoints
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from ink import (
    DEFAULT_REQUIREMENTS,
    ArtifactError,
    ConfigurationError,
    ContractError,
    DecisionResult,
    DecisionSite,
    FallbackResult,
    Ink,
    InkError,
    Outcome,
    PromotionRequirements,
    StorageError,
    wrap,
)
from ink.cli import main as cli_main
from ink.console.server import ConsoleHandler


def test_public_error_hierarchy():
    """Verify InkError base class and error inheritance."""
    assert issubclass(ContractError, InkError)
    assert issubclass(ContractError, ValueError)
    assert issubclass(ArtifactError, InkError)
    assert issubclass(StorageError, InkError)
    assert issubclass(ConfigurationError, InkError)
    assert issubclass(ConfigurationError, ValueError)

    with pytest.raises(ContractError):
        DecisionSite(
            name="",
            state_schema={"x": "string"},
            choices=("a", "b"),
        )

    site = DecisionSite(
        name="test.site",
        state_schema={"flag": "boolean"},
        choices=("yes", "no"),
    )
    with pytest.raises(ContractError):
        site.encode({"flag": "not_a_bool"})


def test_decision_site_description_hash_preservation():
    """Verify description field does not change the cryptographic contract digest."""
    site_no_desc = DecisionSite(
        name="workflow.route",
        state_schema={"tier": "string"},
        choices=("fast", "slow"),
    )
    site_with_desc = DecisionSite(
        name="workflow.route",
        state_schema={"tier": "string"},
        choices=("fast", "slow"),
        description="Human readable documentation about this routing gate",
    )
    assert site_no_desc.version == site_with_desc.version
    assert site_with_desc.description == "Human readable documentation about this routing gate"


def test_wrap_decorator_sync(tmp_path):
    """Verify @ink.wrap decorator on synchronous functions."""
    db_path = str(tmp_path / "wrap_sync.db")
    ink = Ink(db_path)

    site = DecisionSite(
        name="sync.route",
        state_schema={"score": "integer"},
        choices=("accept", "reject"),
    )

    call_count = 0

    @ink.wrap(site)
    def classify_score(state: dict) -> str:
        nonlocal call_count
        call_count += 1
        return "accept" if state["score"] >= 50 else "reject"

    res = classify_score({"score": 80})
    assert isinstance(res, DecisionResult)
    assert res.choice == "accept"
    assert call_count == 1
    assert str(res) == "accept"


def test_wrap_decorator_unpack_and_kwargs(tmp_path):
    """Verify @ink.wrap decorator unpacking and keyword argument state handling."""
    db_path = str(tmp_path / "wrap_unpack.db")
    ink = Ink(db_path)

    site = DecisionSite(
        name="kwargs.route",
        state_schema={"category": "string"},
        choices=("billing", "tech"),
    )

    @ink.wrap(site, unpack=True)
    def route_category(**kwargs) -> str:
        return "billing" if kwargs.get("category") == "money" else "tech"

    choice = route_category(category="money")
    assert choice == "billing"
    assert isinstance(choice, str)


def test_wrap_decorator_async(tmp_path):
    """Verify @ink.wrap decorator on asynchronous coroutines."""
    db_path = str(tmp_path / "wrap_async.db")
    ink = Ink(db_path)

    site = DecisionSite(
        name="async.route",
        state_schema={"priority": "string"},
        choices=("urgent", "normal"),
    )

    @ink.wrap(site)
    async def async_classify(state: dict) -> str:
        await asyncio.sleep(0.001)
        return "urgent" if state["priority"] == "p0" else "normal"

    res = asyncio.run(async_classify({"priority": "p0"}))
    assert isinstance(res, DecisionResult)
    assert res.choice == "urgent"


def test_record_outcome_ergonomics(tmp_path):
    """Verify record_outcome accepts Outcome object or kwargs."""
    db_path = str(tmp_path / "outcomes.db")
    ink = Ink(db_path)

    site = DecisionSite(
        name="test.outcomes",
        state_schema={"val": "integer"},
        choices=("a", "b"),
    )
    res1 = ink.decide(site=site, state={"val": 1}, fallback=lambda: "a")
    res2 = ink.decide(site=site, state={"val": 2}, fallback=lambda: "b")

    # Style 1: Outcome object
    outcome = Outcome(1.0, "v1", "rev1", {"verified": True})
    ink.record_outcome(res1.decision_id, outcome)

    # Style 2: Keyword arguments
    ink.record_outcome(res2.decision_id, quality=0.9, verifier="v2", verifier_version="rev2")

    hist = ink.store.history(site.version)
    assert len(hist) == 2
    assert hist[0]["outcome"]["quality"] == 1.0
    assert hist[1]["outcome"]["quality"] == 0.9


def test_promotion_requirements_defaults():
    """Verify PromotionRequirements sensible defaults."""
    req = PromotionRequirements()
    assert req.min_samples == 10
    assert req.min_quality == 0.8
    assert req.min_confidence == 0.7
    assert req.max_degradation == 0.15
    assert req.comparison_rate == 0.25
    assert req.min_region_samples == 3
    assert req.evaluation_window == 50
    assert DEFAULT_REQUIREMENTS == req


def test_cli_status_and_doctor(tmp_path, capsys):
    """Verify CLI status and doctor commands with and without --json."""
    db_path = str(tmp_path / "cli_test.db")

    # 1. Clean DB status
    ret = cli_main(["status", "--db", db_path])
    assert ret == 0
    out = capsys.readouterr().out
    assert "No decision sites recorded." in out

    ret = cli_main(["status", "--db", db_path, "--json"])
    assert ret == 0
    out = capsys.readouterr().out
    assert json.loads(out) == []

    # 2. Doctor check
    ret = cli_main(["doctor", "--db", db_path, "--json"])
    assert ret == 0
    out = capsys.readouterr().out
    doc = json.loads(out)
    assert "status" in doc
    assert doc["database_path"] == db_path


def test_console_demo_endpoints():
    """Verify ConsoleHandler returns valid data in demo mode."""
    handler = ConsoleHandler
    handler.demo_mode = True

    demo_data = handler._demo_overview(None)
    assert demo_data["is_demo"] is True
    assert demo_data["total_decisions"] > 0
    assert len(demo_data["sites"]) == 3
    assert demo_data["fast_served_rate"] > 0.5
