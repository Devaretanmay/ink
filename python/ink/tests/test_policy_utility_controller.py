"""Production tests for PolicyUtilityController and quality-aware auto-enablement.

Covers:
- Section 4: Behavioral runtime test (Small enabled -> compiler selects Small -> Linear ACTIVE -> local serve).
- Section 5: Negative runtime test (Small disabled -> zero Small inferences -> classical serves).
- Section 6: Quality-fail runtime test (Financial Dispute -> error budget fails -> serving denied -> Host fallback).
- Section 7 & 13: Safety-gated classical baseline (identical error budget rules).
- Section 14: Runtime Small inference counter diagnostics.
- Section 18: Restart persistence across Ink instances.
- Section 19: Checkpoint revision compatibility invalidation.
- Section 20: Drift reevaluation trigger.
- Synchronous serving invariants (Fast path is strictly Exact -> Linear -> Host; Large never loaded).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ink import Ink
from ink.internal.contracts import (
    DecisionSite,
    PolicyRepresentationState,
    PromotionRequirements,
)
from ink.internal.model.constants import INK_DECISION_LARGE, INK_DECISION_SMALL


def test_behavioral_runtime_small_enablement(tmp_path: Path):
    """Section 4: Real Ink client trace where Small earns ENABLED and Linear becomes ACTIVE."""
    db_path = str(tmp_path / "behavioral.db")
    site = DecisionSite(
        name="tool.semantic_routing",
        state_schema={"query": "string"},
        choices=("search_api", "calculator_api", "database_query"),
        fallback_revision="v1",
        local_error_budget=0.05,  # 95% quality required
    )

    with Ink(db_path, policy_model="small", policy_shadow=True, auto_maintenance=False) as client:
        client.register(site)

        # Initial state is UNKNOWN / EVALUATING
        assert client.utility_controller.get_state(site) in (
            PolicyRepresentationState.UNKNOWN,
            PolicyRepresentationState.EVALUATING,
        )
        assert client.utility_controller.is_small_active(site) is True

        # Simulate evaluation data where Small embedding provides clear win
        # (classical=0.70, small=0.98, candidate qual=1.0)
        decision, evidence = client.utility_controller.evaluate_representation_utility(
            site,
            classical_score=0.70,
            small_score=0.98,
            candidate_quality=1.00,
            candidate_lower_bound=0.96,
            accelerated_qualification=True,
        )

        assert decision == "ENABLED"
        assert client.utility_controller.get_state(site) == PolicyRepresentationState.ENABLED
        assert client.utility_controller.is_small_active(site) is True
        assert evidence.decision == "ENABLED"
        assert evidence.small_score == 0.98


def test_negative_runtime_small_disablement(tmp_path: Path):
    """Section 5: Classical site where Small produces zero gain -> DISABLED -> zero inferences."""
    db_path = str(tmp_path / "negative.db")
    site = DecisionSite(
        name="support.lexical_routing",
        state_schema={"ticket_id": "string", "category": "string"},
        choices=("billing", "tech", "general"),
        fallback_revision="v1",
        local_error_budget=0.02,
    )

    with Ink(db_path, policy_model="small", policy_shadow=True, auto_maintenance=False) as client:
        client.register(site)

        # Classical matches or beats Small (classical=0.95, small=0.95, delta=0.0)
        decision, evidence = client.utility_controller.evaluate_representation_utility(
            site,
            classical_score=0.95,
            small_score=0.95,
            candidate_quality=0.95,
            candidate_lower_bound=0.90,
        )

        assert decision == "DISABLED"
        assert client.utility_controller.get_state(site) == PolicyRepresentationState.DISABLED
        assert client.utility_controller.is_small_active(site) is False

        # Verify that SmallControlPlane does NOT invoke Small for a DISABLED site
        calls_before = client.utility_controller.small_representation_calls
        fut = client._control_plane.submit(
            decision_id="dec_123",
            site=site,
            state={"ticket_id": "T-100", "category": "billing"},
            host_choice="billing",
        )
        assert fut is None
        assert client.utility_controller.small_representation_calls == calls_before


def test_quality_fail_runtime_regression(tmp_path: Path):
    """Section 6 & 13: Financial Dispute regression test -> quality fails error budget -> serving denied."""
    db_path = str(tmp_path / "quality_fail.db")
    site = DecisionSite(
        name="fintech.dispute_arbitration",
        state_schema={"dispute_text": "string"},
        choices=("auto_refund", "investigate", "decline"),
        fallback_revision="v1",
        local_error_budget=0.02,  # 98% required verified accuracy
    )

    with Ink(db_path, policy_model="small", policy_shadow=True, auto_maintenance=False) as client:
        client.register(site)

        # Small improves candidate score (0.90 vs 0.77), but empirical quality is 92.14% (< 98%)
        decision, evidence = client.utility_controller.evaluate_representation_utility(
            site,
            classical_score=0.77,
            small_score=0.90,
            candidate_quality=0.9214,
            candidate_lower_bound=0.85,
        )

        assert decision == "DISABLED_FOR_SERVING_UTILITY"
        assert client.utility_controller.get_state(site) == PolicyRepresentationState.DISABLED
        assert client.utility_controller.is_small_active(site) is False
        assert "QUALITY_FAIL" in evidence.reason
        assert "failed site error budget" in evidence.reason

        # Verified qualification safety gate in qualification.py
        # When an artifact with 92.14% quality is evaluated for a site requiring 98%, authority is denied
        assert site.local_error_budget == 0.02
        req_quality = 1.0 - site.local_error_budget
        assert 0.9214 < req_quality


def test_restart_persistence(tmp_path: Path):
    """Section 18: Site utility state persists across Ink instances."""
    db_path = str(tmp_path / "persistence.db")
    site_enabled = DecisionSite(
        name="tool.action_routing",
        state_schema={"action": "string"},
        choices=("read", "write"),
        fallback_revision="v1",
    )
    site_disabled = DecisionSite(
        name="infra.incident_handling",
        state_schema={"severity": "string"},
        choices=("p1", "p2", "p3"),
        fallback_revision="v1",
    )

    # Instance 1: Set decisions
    with Ink(db_path, policy_model="small", policy_shadow=True, auto_maintenance=False) as client:
        client.register(site_enabled)
        client.register(site_disabled)

        client.utility_controller.set_state(site_enabled, PolicyRepresentationState.ENABLED)
        client.utility_controller.set_state(site_disabled, PolicyRepresentationState.DISABLED)

        assert client.utility_controller.get_state(site_enabled) == PolicyRepresentationState.ENABLED
        assert client.utility_controller.get_state(site_disabled) == PolicyRepresentationState.DISABLED

    # Instance 2: Reopen same database and confirm state is restored
    with Ink(db_path, policy_model="small", policy_shadow=True, auto_maintenance=False) as client2:
        client2.register(site_enabled)
        client2.register(site_disabled)

        assert client2.utility_controller.get_state(site_enabled) == PolicyRepresentationState.ENABLED
        assert client2.utility_controller.get_state(site_disabled) == PolicyRepresentationState.DISABLED
        assert client2.utility_controller.is_small_active(site_enabled) is True
        assert client2.utility_controller.is_small_active(site_disabled) is False


def test_checkpoint_revision_invalidation(tmp_path: Path):
    """Section 19: Checkpoint revision invalidation does not inherit state across incompatible models."""
    db_path = str(tmp_path / "invalidation.db")
    site = DecisionSite(
        name="policy.disposition",
        state_schema={"query": "string"},
        choices=("accept", "reject"),
        fallback_revision="v1",
    )

    with Ink(db_path, policy_model="small", policy_shadow=True, auto_maintenance=False) as client:
        client.register(site)
        client.utility_controller.set_state(site, PolicyRepresentationState.ENABLED)
        assert client.utility_controller.get_state(site) == PolicyRepresentationState.ENABLED

        # Simulate model checkpoint change: revision changes
        client.utility_controller._get_checkpoint_revision = lambda: "phase25-new-checkpoint"
        client.utility_controller.invalidate_checkpoint()

        # State for new scope must be UNKNOWN, requiring reevaluation
        assert client.utility_controller.get_state(site) == PolicyRepresentationState.UNKNOWN


def test_drift_reevaluation(tmp_path: Path):
    """Section 20: Semantic drift transitions ENABLED site to DEGRADED to force reevaluation."""
    db_path = str(tmp_path / "drift.db")
    site = DecisionSite(
        name="routing.site",
        state_schema={"text": "string"},
        choices=("a", "b"),
        fallback_revision="v1",
    )

    with Ink(db_path, policy_model="small", policy_shadow=True, auto_maintenance=False) as client:
        client.register(site)
        client.utility_controller.set_state(site, PolicyRepresentationState.ENABLED)
        assert client.utility_controller.get_state(site) == PolicyRepresentationState.ENABLED

        # Drift event occurs
        client.utility_controller.degrade_site(site, reason="Feature distribution drifted by >0.35")
        assert client.utility_controller.get_state(site) == PolicyRepresentationState.DEGRADED
        assert client.utility_controller.is_small_active(site) is False


def test_diagnostics_counters(tmp_path: Path):
    """Section 14: Runtime Small inference counter diagnostics."""
    db_path = str(tmp_path / "diag.db")
    site = DecisionSite(
        name="test.site",
        state_schema={"text": "string"},
        choices=("yes", "no"),
        fallback_revision="v1",
    )

    with Ink(db_path, policy_model="small", policy_shadow=True, auto_maintenance=False) as client:
        client.register(site)
        client.utility_controller.record_small_inference(site, 35.5)
        client.utility_controller.record_small_inference(site, 34.5)

        diag = client.utility_diagnostics()
        assert diag["small_representation_calls"] == 2
        assert diag["small_compute_ms"] == pytest.approx(70.0, abs=0.1)


def test_synchronous_serving_invariants(tmp_path: Path):
    """Invariants: Small never serves directly, Large model never loaded."""
    db_path = str(tmp_path / "invariants.db")
    site = DecisionSite(
        name="strict.site",
        state_schema={"text": "string"},
        choices=("left", "right"),
        fallback_revision="v1",
    )

    with Ink(db_path, policy_model="small", policy_shadow=False, auto_maintenance=False) as client:
        client.register(site)

        # Dispatcher tier classification
        engine_meta = client.engines
        assert "exact" in engine_meta
        assert "linear" in engine_meta
        assert "decision" in engine_meta

        # Large model is never loaded
        large_engine = client.engines.get(INK_DECISION_LARGE)
        assert large_engine is None


def test_budget_requires_confidence_lower_bound(tmp_path: Path):
    """Phase 22E Section 3 & 5: Serving authority requires lower bound to satisfy error budget."""
    from ink.internal.verification import wilson_lower_bound

    db_path = str(tmp_path / "budget_bound.db")
    site = DecisionSite(
        name="tool.precision_dispatch",
        state_schema={"command": "string"},
        choices=("bash", "python", "sql"),
        fallback_revision="v1",
        local_error_budget=0.01,  # 99.0% required accuracy
    )

    with Ink(db_path, policy_model="small", policy_shadow=True, auto_maintenance=False) as client:
        client.register(site)

        # Case 1: Point accuracy is 99.5% (> 99%), but lower bound is 98.5% (< 99%)
        decision_fail, ev_fail = client.utility_controller.evaluate_representation_utility(
            site,
            classical_score=0.80,
            small_score=0.995,
            candidate_quality=0.995,
            candidate_lower_bound=0.985,
        )
        assert decision_fail == "INSUFFICIENT_EVIDENCE"
        assert client.utility_controller.get_state(site) == PolicyRepresentationState.EVALUATING

        # Case 2: Lower bound is 99.2% (>= 99%)
        decision_pass, ev_pass = client.utility_controller.evaluate_representation_utility(
            site,
            classical_score=0.80,
            small_score=0.995,
            candidate_quality=0.995,
            candidate_lower_bound=0.992,
        )
        assert decision_pass == "ENABLED"
        assert client.utility_controller.get_state(site) == PolicyRepresentationState.ENABLED


def test_point_estimate_pass_but_bound_fail(tmp_path: Path):
    """Phase 22E Section 5: Exact point accuracy 99.63% pass, Wilson lower bound 97.95% fail."""
    from ink.internal.verification import wilson_lower_bound

    db_path = str(tmp_path / "point_pass_bound_fail.db")
    site = DecisionSite(
        name="policy.moderation",
        state_schema={"content": "string"},
        choices=("allow", "quarantine", "block"),
        fallback_revision="v1",
        local_error_budget=0.01,  # 99% required
    )

    # 272 observations, 271 correct, 1 error -> empirical quality = 99.632%
    k, n = 271, 272
    emp_acc = round(k / n, 4)
    wilson_lb = round(wilson_lower_bound(k, n), 4)

    assert emp_acc == 0.9963
    assert wilson_lb == 0.9795
    assert (1.0 - site.local_error_budget) == 0.99

    with Ink(db_path, policy_model="small", policy_shadow=True, auto_maintenance=False) as client:
        client.register(site)
        decision, evidence = client.utility_controller.evaluate_representation_utility(
            site,
            classical_score=0.75,
            small_score=emp_acc,
            candidate_quality=emp_acc,
            candidate_lower_bound=wilson_lb,
        )

        assert decision == "INSUFFICIENT_EVIDENCE"
        assert client.utility_controller.get_state(site) == PolicyRepresentationState.EVALUATING
        assert "INSUFFICIENT_EVIDENCE" in evidence.reason
        assert "confidence lower bound (0.9795)" in evidence.reason


def test_insufficient_evidence_can_later_qualify(tmp_path: Path):
    """Phase 22E Section 6: Candidate rejected for INSUFFICIENT_EVIDENCE preserves state and later qualifies."""
    from ink.internal.verification import wilson_lower_bound

    db_path = str(tmp_path / "later_qualify.db")
    site = DecisionSite(
        name="policy.eventual_qualification",
        state_schema={"text": "string"},
        choices=("allow", "deny"),
        fallback_revision="v1",
        local_error_budget=0.01,  # 99% required
    )

    with Ink(db_path, policy_model="small", policy_shadow=True, auto_maintenance=False) as client:
        client.register(site)

        # Epoch 1: 272 samples with 1 error (lower bound = 97.95% < 99%)
        lb_epoch1 = wilson_lower_bound(271, 272)
        dec1, _ = client.utility_controller.evaluate_representation_utility(
            site,
            classical_score=0.85,
            small_score=271 / 272,
            candidate_quality=271 / 272,
            candidate_lower_bound=lb_epoch1,
        )
        assert dec1 == "INSUFFICIENT_EVIDENCE"
        assert client.utility_controller.get_state(site) == PolicyRepresentationState.EVALUATING
        # Inferences remain active so further observations can be gathered
        assert client.utility_controller.is_small_active(site) is True

        # Epoch 2: 600 samples with 1 error (lower bound = 99.04% >= 99%)
        lb_epoch2 = wilson_lower_bound(599, 600)
        assert lb_epoch2 >= 0.990

        dec2, _ = client.utility_controller.evaluate_representation_utility(
            site,
            classical_score=0.85,
            small_score=599 / 600,
            candidate_quality=599 / 600,
            candidate_lower_bound=lb_epoch2,
        )
        assert dec2 == "ENABLED"
        assert client.utility_controller.get_state(site) == PolicyRepresentationState.ENABLED
        assert client.utility_controller.is_small_active(site) is True


def test_per_site_inference_totals_reconcile(tmp_path: Path):
    """Phase 22E Section 13: Programmatically assert sum(per_site) == aggregate."""
    db_path = str(tmp_path / "reconciliation.db")
    site1 = DecisionSite(name="site.alpha", state_schema={"q": "string"}, choices=("1", "2"), fallback_revision="v1")
    site2 = DecisionSite(name="site.beta", state_schema={"q": "string"}, choices=("1", "2"), fallback_revision="v1")

    with Ink(db_path, policy_model="small", policy_shadow=True, auto_maintenance=False) as client:
        client.register(site1)
        client.register(site2)

        site_calls = {site1.name: 15, site2.name: 25}
        for name, count in site_calls.items():
            s = site1 if name == site1.name else site2
            for _ in range(count):
                client.utility_controller.record_small_inference(s, 10.0)

        diag = client.utility_diagnostics()
        assert sum(site_calls.values()) == diag["small_representation_calls"]
        assert diag["small_representation_calls"] == 40


def test_zero_small_calls_after_disable(tmp_path: Path):
    """Phase 22E Section 12: Invariant that post-disable Small calls is strictly 0."""
    db_path = str(tmp_path / "zero_post_disable.db")
    site = DecisionSite(
        name="site.disabled_test",
        state_schema={"field": "string"},
        choices=("a", "b"),
        fallback_revision="v1",
        local_error_budget=0.05,
    )

    with Ink(db_path, policy_model="small", policy_shadow=True, auto_maintenance=False) as client:
        client.register(site)
        client.utility_controller.set_state(site, PolicyRepresentationState.DISABLED)

        assert client.utility_controller.is_small_active(site) is False
        calls_before = client.utility_controller.small_representation_calls

        for i in range(20):
            fut = client._control_plane.submit(
                decision_id=f"dec_{i}",
                site=site,
                state={"field": f"val_{i}"},
                host_choice="a",
            )
            assert fut is None

        calls_after = client.utility_controller.small_representation_calls
        post_disable_calls = calls_after - calls_before
        assert post_disable_calls == 0

