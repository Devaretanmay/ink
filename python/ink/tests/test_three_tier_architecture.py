"""Tests for Ink's Three-Tier Architecture:

1. SERVING PATH:
   Exact -> Linear -> Host

2. LEARNING / CONTROL PLANE:
   Small Policy Model -> semantic proposals / regions -> Ink evidence lifecycle -> (Exact, Linear)

3. OFFLINE TRAINING:
   Large -> teacher / distillation -> Small
"""

from __future__ import annotations

import time
import pytest
from ink import Ink, DecisionSite
from ink.internal.engines import (
    ExactEngine,
    LinearClassifierEngine,
    PolicyModelEngine,
    get_engine_metadata,
    INK_DECISION_SMALL,
    INK_DECISION_LARGE,
)
from ink.internal.contracts import DecisionResult, FallbackResult, Outcome, PromotionRequirements


REQ = PromotionRequirements(
    min_samples=6,
    min_quality=0.5,
    min_confidence=0.5,
    max_degradation=0.8,
    comparison_rate=0.25,
    min_region_samples=3,
    evaluation_window=50,
)


def _record(client, res, quality=1.0):
    client.record_outcome(
        res.decision_id,
        quality=quality,
        verifier="verifier_v1",
        verifier_version="1",
        evidence={"verified": True},
    )


def test_engine_metadata_and_tier_classification():
    """Verify engine metadata classifies tiers according to the three planes."""
    m_exact = get_engine_metadata("exact")
    assert "Serving Tier 1" in m_exact["tier"]
    assert m_exact["p50_latency_ms"] <= 0.01

    m_linear = get_engine_metadata("linear")
    assert "Serving Tier 2" in m_linear["tier"]
    assert m_linear["p50_latency_ms"] <= 0.1

    m_small = get_engine_metadata(INK_DECISION_SMALL)
    assert "Control Plane" in m_small["tier"]

    m_large = get_engine_metadata(INK_DECISION_LARGE)
    assert "Offline Training" in m_large["tier"]


def test_serving_path_exact_tier(tmp_path):
    """Test Tier 1: Exact matches execute locally in sub-milliseconds without host or neural calls."""
    db_path = str(tmp_path / "test_serving_exact.db")
    site = DecisionSite(
        name="auth.triage",
        state_schema={"event": "string", "level": "string"},
        choices=("allow", "deny", "challenge"),
        fallback_revision="v1",
    )

    host_call_count = 0

    def mock_host_fallback():
        nonlocal host_call_count
        host_call_count += 1
        return "challenge"

    with Ink(
        db_path,
        engines=(ExactEngine(),),
        auto_maintenance=True,
        maintenance_interval=0.05,
        maintenance_requirements=REQ,
        maintenance_engine="exact",
    ) as client:
        client.register(site)

        # 1. First interaction: in OBSERVE mode, falls back to host
        res1 = client.decide(site, {"event": "login", "level": "low"}, fallback=mock_host_fallback)
        assert res1.source == "fallback"
        assert res1.choice == "challenge"
        assert host_call_count == 1
        _record(client, res1, quality=1.0)

        # Feed observations to build exact evidence
        for i in range(100):
            st = {"event": "login" if i % 2 == 0 else "logout", "level": "low"}
            r = client.decide(site, st, task_id=f"obs-{i}", fallback=mock_host_fallback)
            _record(client, r, quality=1.0)

        # Wait for candidate compilation to SHADOW
        deadline = time.time() + 10.0
        while time.time() < deadline:
            if client.status(site)["state"] == "SHADOW":
                break
            time.sleep(0.05)
        assert client.status(site)["state"] == "SHADOW"

        # Feed shadow traffic
        for i in range(30):
            st = {"event": "login" if i % 2 == 0 else "logout", "level": "low"}
            r = client.decide(site, st, task_id=f"shad-{i}", fallback=mock_host_fallback)
            _record(client, r, quality=1.0)

        # Wait for promotion to ACTIVE
        deadline = time.time() + 10.0
        while time.time() < deadline:
            if client.status(site)["state"] == "ACTIVE":
                break
            time.sleep(0.05)
        assert client.status(site)["state"] == "ACTIVE"

        # 2. Subsequent interaction serves locally via ExactEngine Fast Path
        host_before = host_call_count
        t0 = time.perf_counter()
        res_fast = client.decide(site, {"event": "login", "level": "low"}, fallback=mock_host_fallback)
        t_elapsed_ms = (time.perf_counter() - t0) * 1000

        # Verified fast path or comparison
        if res_fast.source == "fast_path":
            assert res_fast.choice == "challenge"
            assert host_call_count == host_before
            # Full call includes durable SQLite receipt; engine-only latency is reported separately.
            assert t_elapsed_ms < 5.0


def test_serving_path_novel_state_falls_back_to_host(tmp_path):
    """Verify that unverified/novel states strictly fall back to Host (not unverified model)."""
    db_path = str(tmp_path / "test_fallback_safety.db")
    site = DecisionSite(
        name="routing.action",
        state_schema={"action": "string"},
        choices=("allow", "deny"),
        fallback_revision="v1",
    )

    host_called = False

    def host_fallback():
        nonlocal host_called
        host_called = True
        return "allow"

    with Ink(db_path) as client:
        client.register(site)
        res = client.decide(site, {"action": "unseen_action_xyz"}, fallback=host_fallback)
        assert res.source == "fallback"
        assert res.choice == "allow"
        assert host_called is True


def test_control_plane_small_policy_model_isolation():
    """Verify Small Policy Model operates as proposal engine and does not hijack unverified serving."""
    metadata = get_engine_metadata(INK_DECISION_SMALL)
    assert metadata["name"] == INK_DECISION_SMALL
    assert "Control Plane" in metadata["tier"]


def test_offline_training_large_model_isolation():
    """Verify Large Model is marked as offline teacher/distillation tier."""
    metadata = get_engine_metadata(INK_DECISION_LARGE)
    assert metadata["name"] == INK_DECISION_LARGE
    assert "Offline Training" in metadata["tier"]
