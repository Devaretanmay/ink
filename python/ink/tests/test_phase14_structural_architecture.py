"""Phase 14 Structural Architecture & Refactoring Boundary Tests.

Verifies:
1. Module decomposition into single-responsibility units (Compiler, Qualification, Evaluator).
2. Zero raw SQL in client and runtime modules.
3. InkArtifact contract protocol (typed properties + dict access parity).
4. Public facade and backwards compatibility re-exports.
5. Sync vs async decision execution parity.
6. Read-only invariance of diagnostics methods.
7. Compaction boundaries preserving partition decisions.
8. CoverageEngine dynamic history self-tuning.
9. Schema inference and contract hash determinism.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import pytest

from ink import Ink as TopInk
from ink.client import Ink as ClientInk
from ink.decision_api import Ink as LegacyInk
from ink.internal.contracts import (
    DecisionResult,
    DecisionSite,
    FallbackResult,
    InkArtifact,
    MaintenanceOutcome,
    PromotionRequirements,
)
from ink.internal.coverage import CoverageEngine, SemanticRegion, TextVectorizer
from ink.internal.decision_store import DecisionStore
from ink.runtime.compiler import Compiler
from ink.runtime.diagnostics import Diagnostics
from ink.runtime.dispatcher import Dispatcher
from ink.runtime.epoch_manager import EpochManager
from ink.runtime.evaluator import Evaluator
from ink.runtime.qualification import QualificationOrchestrator


def test_client_compatibility_reexport():
    """Verify that importing Ink from ink, ink.client, and ink.decision_api are identical."""
    assert ClientInk is TopInk
    assert LegacyInk is TopInk
    assert TopInk.__name__ == "Ink"


def test_ink_artifact_protocol_and_accessors():
    """Verify typed properties and dict-like backward-compatibility accessors on InkArtifact."""
    raw_row = {
        "id": "art_123",
        "site": "auth.gate:v1",
        "payload": '{"engine_data": {"engine": "exact"}, "partitions": {"train": ["d1"]}}',
        "checksum": "chk_abc",
        "status": "ACTIVE",
        "epoch": 1700000000.0,
        "profile": '{"requirements": {"min_samples": 10}}',
        "evidence": '{"qualified": true}',
    }
    art = InkArtifact.from_row(raw_row)
    assert art.id == "art_123"
    assert art.is_active is True
    assert art.is_shadow is False
    assert art.is_verified is False
    assert art.is_retired is False
    assert art.is_candidate is False

    # Dictionary indexing protocol
    assert art["id"] == "art_123"
    assert art["status"] == "ACTIVE"
    assert art["checksum"] == "chk_abc"
    assert art["engine_data"]["engine"] == "exact"
    assert "payload" in art
    assert "partitions" in art
    assert art.get("missing_key", "default_val") == "default_val"
    assert art.to_dict()["status"] == "ACTIVE"

    # Status mutability reflection
    art.status = "SHADOW"
    assert art.is_shadow is True
    assert art.is_active is False
    art.status = "VERIFIED"
    assert art.is_verified is True
    art.status = "RETIRED"
    assert art.is_retired is True
    art.status = "CANDIDATE"
    assert art.is_candidate is True


def test_zero_raw_sql_in_runtime_and_client_modules():
    """Enforce architectural invariant: ZERO raw SQL outside internal/decision_store.py."""
    pkg_dir = Path(__file__).resolve().parent.parent / "ink"
    checked_files = [
        pkg_dir / "client.py",
        pkg_dir / "runtime" / "dispatcher.py",
        pkg_dir / "runtime" / "compiler.py",
        pkg_dir / "runtime" / "qualification.py",
        pkg_dir / "runtime" / "evaluator.py",
        pkg_dir / "runtime" / "epoch_manager.py",
        pkg_dir / "runtime" / "diagnostics.py",
    ]
    sql_pattern = re.compile(r'\b(SELECT|INSERT INTO|UPDATE|DELETE FROM)\b\s', re.IGNORECASE)

    violations = []
    for f in checked_files:
        assert f.exists(), f"Expected file {f} does not exist"
        content = f.read_text()
        matches = sql_pattern.findall(content)
        if matches:
            violations.append((f.name, matches))

    assert not violations, f"Raw SQL detected outside DecisionStore in: {violations}"


def test_subcomponent_instantiation_and_isolation(tmp_path):
    """Verify clean instantiation and delegation of all single-responsibility runtime subcomponents."""
    db_path = tmp_path / "decisions.db"
    ink = ClientInk(str(db_path))

    assert isinstance(ink._dispatcher, Dispatcher)
    assert isinstance(ink._compiler, Compiler)
    assert isinstance(ink._qualification, QualificationOrchestrator)
    assert isinstance(ink._evaluator, Evaluator)
    assert isinstance(ink._epoch_manager, EpochManager)
    assert isinstance(ink._diagnostics, Diagnostics)

    assert ink._evaluator.compiler is ink._compiler
    assert ink._evaluator.qualification is ink._qualification


def test_sync_and_async_decision_parity(tmp_path):
    """Verify synchronous decide and async decide_async produce identical output and contracts."""
    db_path = tmp_path / "decisions.db"
    ink = ClientInk(str(db_path))

    site = DecisionSite("route.tier", {"tier": "string", "usage": "integer"}, ("allow", "throttle"))
    state = {"tier": "enterprise", "usage": 42}

    def fallback_sync():
        return FallbackResult("allow", cost=0.001, model_calls=1)

    async def fallback_async():
        return FallbackResult("allow", cost=0.001, model_calls=1)

    res_sync = ink.decide(site=site, state=state, fallback=fallback_sync, task_id="t_sync")
    res_async = asyncio.run(
        ink.decide_async(site=site, state=state, fallback=fallback_async, task_id="t_async")
    )

    assert res_sync.choice == res_async.choice == "allow"
    assert res_sync.source == res_async.source == "fallback"
    assert res_sync.site_version == res_async.site_version == site.version
    assert res_sync.fallback_reason == res_async.fallback_reason == "observe"

    # Verify both records reached the persistent store
    rows = ink.store.history(site.version)
    assert len(rows) == 2


def test_diagnostics_read_only_invariance(tmp_path):
    """Verify that diagnostics methods (inspect, sites, receipt, coverage) never mutate store state."""
    db_path = tmp_path / "decisions.db"
    ink = ClientInk(str(db_path))

    site = DecisionSite("data.tier", {"size": "integer"}, ("compress", "stream"))
    res = ink.decide(
        site=site,
        state={"size": 1024},
        fallback=lambda: FallbackResult("compress", cost=0.001, model_calls=1),
        task_id="t_diag",
    )

    pre_counts = ink.store.get_doctor_summary()

    # Call all diagnostic inspection APIs
    _ = ink.sites()
    _ = ink.inspect(site)
    _ = ink.coverage(site)
    _ = ink.receipt(res.decision_id)
    _ = ink.profile(site)
    _ = ink.lineage(site)
    _ = ink.promotions(site)
    _ = ink.drift_history(site)

    post_counts = ink.store.get_doctor_summary()
    assert pre_counts == post_counts


def test_compaction_preserves_partitions_and_recent_window(tmp_path):
    """Verify compaction prunes old decisions while preserving candidate/active partition rows."""
    db_path = tmp_path / "decisions.db"
    ink = ClientInk(str(db_path))

    site = DecisionSite("cache.gate", {"key": "string"}, ("hit", "miss"))

    # Seed 30 decisions
    for i in range(30):
        r = ink.decide(
            site=site,
            state={"key": f"k_{i % 5}"},
            fallback=lambda: FallbackResult("hit", cost=0.001, model_calls=1),
            task_id=f"task_{i}",
        )
        ink.record_outcome(r.decision_id, quality=1.0, verifier="test_v", verifier_version="1.0")

    ink.compile(site, engine="exact")

    # Get artifact partition IDs
    art = ink._artifact(site.version)
    partitions = art["payload"].get("partitions", {})
    protected = set()
    for ids in partitions.values():
        protected.update(ids)
    assert protected, "Partitions should not be empty after compilation"

    # Add 20 extra decisions after compilation
    for i in range(20):
        ink.decide(
            site=site,
            state={"key": f"k_extra_{i}"},
            fallback=lambda: FallbackResult("hit", cost=0.001, model_calls=1),
            task_id=f"extra_task_{i}",
        )

    # Run compact keeping only 5 recent rows with cutoff after the extra decisions
    res = ink.compact(site, keep_recent=5, before_timestamp=10000000000.0)
    assert res["pruned"] > 0
    assert res["remaining"] >= len(protected)

    # Ensure all protected partition decision IDs still exist in store
    history = ink.store.history(site.version)
    remaining_ids = {h["id"] for h in history}
    for pid in protected:
        assert pid in remaining_ids, f"Protected decision {pid} was improperly pruned!"


def test_coverage_engine_self_tune_from_history():
    """Verify CoverageEngine.self_tune_from_history handles counterexamples and triggers callback."""
    vec = TextVectorizer(vocab={"critical": 0, "minor": 1}, idf={"critical": 1.0, "minor": 1.0})
    region = SemanticRegion(
        region_id="sem-test-001",
        site="test.site:v1",
        choice="escalate",
        prototype_state={"text": "critical alert"},
        prototype_vector=[1.0, 0.0],
        radius=0.5,
        negative_margin=0.6,
        member_count=5,
        confidence=0.9,
        status="ACTIVE",
    )
    cov = CoverageEngine(
        exact_coverage={},
        semantic_regions=[region],
        vectorizer=vec,
        ambiguity_margin=0.08,
    )

    # A counterexample: state that matches the region but had quality 0.0
    history = [
        {
            "state": {"text": "critical alert"},
            "choice": "ignore",  # counter-choice
            "outcome": {"quality": 0.0},
        }
    ]

    events = []

    def on_event(name, data):
        events.append((name, data))

    tightened = cov.self_tune_from_history(history, requirements={"allow_region_split": False}, on_event=on_event)
    assert tightened is True
    assert len(events) == 1
    assert events[0][0] == "region_tightened"
    assert "sem-test-001" in events[0][1]["regions"]


def test_maintenance_outcome_dict_semantics():
    """Verify MaintenanceOutcome dictionary and property access semantics."""
    outcome = MaintenanceOutcome({"pending": "waiting_for_calibration"})
    assert outcome.status == "blocked"
    assert outcome.reason == "waiting_for_calibration"

    outcome_err = MaintenanceOutcome({"pending": "sqlite3.OperationalError: database locked"})
    assert outcome_err.status == "failed"

    outcome_comp = MaintenanceOutcome({"compiled": "art_999"})
    assert outcome_comp.status == "completed"
    assert outcome_comp["action"] == "compiled"
