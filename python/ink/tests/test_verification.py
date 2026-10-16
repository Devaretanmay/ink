"""Tests for evidence verification integrity, statistical bounds, and denominator preservation."""

import pytest
from dataclasses import asdict

from ink.internal.contracts import Outcome, canonical
from ink.internal.engines import ExactEngine
from ink.internal.verification import (
    EvidenceStatus,
    lower_bound,
    passes,
    statistics,
    verify_rows,
    wilson_lower_bound,
    wilson_upper_bound,
)


def test_wilson_bounds_mathematical_properties():
    """Verify Wilson score confidence interval boundary properties."""
    # N = 0
    assert wilson_lower_bound(0, 0) == 0.0
    assert wilson_upper_bound(0, 0) == 0.0

    # 0 successes out of 100
    assert wilson_lower_bound(0, 100) == 0.0
    assert 0.0 < wilson_upper_bound(0, 100) < 0.05

    # 100 successes out of 100 (Wilson lower is strictly less than 1.0, reflecting uncertainty)
    w_low_100 = wilson_lower_bound(100, 100)
    assert 0.95 < w_low_100 < 1.0
    assert wilson_upper_bound(100, 100) == 1.0

    # Denominator effect: 10/10 vs 10/100
    w_10_of_10 = wilson_lower_bound(10, 10)
    w_10_of_100 = wilson_lower_bound(10, 100)
    assert w_10_of_10 > 0.65
    assert w_10_of_100 < 0.10
    # Omitting 90 disagreements would falsely boost confidence from ~0.055 to ~0.722!
    assert w_10_of_10 > 5 * w_10_of_100


def test_exact_engine_disagreement_without_verifier_preserves_denominator():
    """When verifier is None, disagreements must NOT be silently skipped.

    They must be preserved in the denominator and count as unverified disagreements.
    """
    engine = ExactEngine()
    choices = ("allow", "deny")

    # Payload with coverage for 10 distinct states
    table = {}
    coverage = {}
    for i in range(10):
        state_key = canonical({"user_id": i})
        # Candidate engine predicts 'allow' for all 10 states
        table[state_key] = {"choice": "allow", "probability": 1.0}
        coverage[state_key] = {"confidence": 0.95, "choice": "allow", "samples": 10}

    payload = {
        "choices": choices,
        "coverage": coverage,
        "engine_data": {
            "engine": "exact",
            "table": table,
        },
    }

    # Input rows: Host chose 'allow' for 3 rows, but 'deny' for 7 rows
    rows = []
    for i in range(10):
        host_choice = "allow" if i < 3 else "deny"
        rows.append(
            {
                "id": f"row-{i}",
                "task": f"task-{i}",
                "state": {"user_id": i},
                "choice": host_choice,
                "outcome": {
                    "quality": 1.0,
                    "verifier": "observed_host",
                    "verifier_version": "1",
                    "evidence": {"observed": True},
                },
            }
        )

    # Execute verify_rows with verifier=None
    results = verify_rows(rows, engine, payload, verifier=None)

    # All 10 tasks must be present - none should be dropped
    assert len(results) == 10

    # 3 agreements, 7 disagreements
    agreements = [r for r in results if r["agreement"]]
    disagreements = [r for r in results if not r["agreement"]]
    assert len(agreements) == 3
    assert len(disagreements) == 7

    # Check evidence status classification
    for r in agreements:
        assert r["status"] == EvidenceStatus.OBSERVED_AGREEMENT
        assert r["candidate"]["quality"] == 1.0

    for r in disagreements:
        assert r["status"] == EvidenceStatus.UNVERIFIED_DISAGREEMENT
        assert r["candidate"]["quality"] == 0.0  # Conservative lower bound attribution
        assert r["candidate"]["evidence"]["unverified"] is True

    # Compute statistics
    stats = statistics(results)

    # Samples must be 10, NOT 3
    assert stats["samples"] == 10
    assert stats["agreement"] == pytest.approx(0.3)
    assert stats["observed_positive"] == 3
    assert stats["observed_negative"] == 0  # Not proven incorrect, but unverified
    assert stats["unverified_disagreements"] == 7
    assert stats["unknown"] == 7

    # Wilson lower bound must be computed over 10 samples (penalized by disagreements)
    assert stats["quality_wilson_lower"] == pytest.approx(wilson_lower_bound(3, 10))
    assert stats["quality_wilson_lower"] < 0.20


def test_independent_verifier_evaluates_divergent_choices():
    """When an independent verifier is provided, candidate choices that differ from Host
    are evaluated by the verifier directly, not treated as unverified.
    """
    engine = ExactEngine()
    choices = ("route_a", "route_b")

    state = {"query": "pricing"}
    key = canonical(state)
    payload = {
        "choices": choices,
        "coverage": {key: {"confidence": 0.9, "choice": "route_a", "samples": 5}},
        "engine_data": {
            "engine": "exact",
            "table": {key: {"choice": "route_a", "probability": 1.0}},
        },
    }

    # Host chose route_b in production and failed (quality 0.0)
    # Candidate predicts route_a
    rows = [
        {
            "id": "row-1",
            "task": "task-1",
            "state": state,
            "choice": "route_b",
            "outcome": {
                "quality": 0.0,
                "verifier": "test_verifier",
                "verifier_version": "v1",
                "evidence": {},
            },
        }
    ]

    # Verifier says route_a is correct (1.0) and route_b is incorrect (0.0)
    def custom_verifier(s, c):
        q = 1.0 if c == "route_a" else 0.0
        return Outcome(
            quality=q, verifier="test_verifier", verifier_version="v1", evidence={"choice": c}
        )

    results = verify_rows(rows, engine, payload, verifier=custom_verifier)

    assert len(results) == 1
    rec = results[0]
    assert rec["agreement"] is False  # Candidate differed from Host
    assert (
        rec["status"] == EvidenceStatus.VERIFIED_SUCCESS
    )  # But verifier proved candidate was right!
    assert rec["candidate"]["quality"] == 1.0
    assert rec["baseline"]["quality"] == 0.0

    stats = statistics(results)
    assert stats["samples"] == 1
    assert stats["observed_positive"] == 1
    assert stats["observed_negative"] == 0
    assert stats["delta"] == 1.0  # Candidate improved over Host by +1.0


def test_missing_outcomes_retained_and_penalize_qualification():
    """Verify that rows with missing outcomes are NOT dropped from verify_rows and
    severely penalize statistics confidence bounds.
    """
    engine = ExactEngine()
    choices = ("opt1", "opt2")
    state_0 = {"param": "alpha"}
    state_1 = {"param": "beta"}

    table = {
        canonical(state_0): {"choice": "opt1", "probability": 1.0},
        canonical(state_1): {"choice": "opt1", "probability": 1.0},
    }
    coverage = {
        canonical(state_0): {"confidence": 0.9, "choice": "opt1", "samples": 5},
        canonical(state_1): {"confidence": 0.9, "choice": "opt1", "samples": 5},
    }
    payload = {
        "choices": choices,
        "coverage": coverage,
        "engine_data": {"engine": "exact", "table": table},
    }

    # 1 task has outcome; 9 tasks have missing outcomes (None)
    rows = [
        {
            "id": "row-0",
            "task": "task-0",
            "state": state_0,
            "choice": "opt1",
            "outcome": {"quality": 1.0, "verifier": "obs", "verifier_version": "1", "evidence": {}},
        }
    ]
    for i in range(1, 10):
        rows.append(
            {
                "id": f"row-{i}",
                "task": f"task-{i}",
                "state": state_1,
                "choice": "opt1",
                "outcome": None,  # Ground truth missing
            }
        )

    results = verify_rows(rows, engine, payload, verifier=None)
    # Crucial invariant: all 10 rows must be preserved in results
    assert len(results) == 10
    missing_results = [r for r in results if r["status"] == EvidenceStatus.UNVERIFIABLE]
    assert len(missing_results) == 9
    for r in missing_results:
        assert r["candidate"] is None
        assert r["baseline"] is None

    stats = statistics(results)
    # The denominator MUST be 10, not 1!
    assert stats["samples"] == 10
    assert stats["observed_positive"] == 1
    assert stats["unknown"] == 9
    # Wilson lower bound with 1 success out of 10 tasks must be severely penalized (< 0.15)
    assert stats["quality_wilson_lower"] < 0.15
    # Hoeffding lower bound accounts for all 10 tasks (effective quality 0.1)
    assert stats["quality_lower"] == 0.0


def test_mixed_outcome_multi_step_tasks_penalize_qualification():
    """Verify that multi-step tasks with mixed outcomes cannot be improperly promoted.
    
    If any step in a multi-step task has an unverified disagreement or missing outcome,
    the task cannot be counted as an observed positive.
    """
    engine = ExactEngine()
    choices = ("step_a", "step_b")

    s1 = {"step": 1}
    s2 = {"step": 2}

    table = {
        canonical(s1): {"choice": "step_a", "probability": 1.0},
        canonical(s2): {"choice": "step_a", "probability": 1.0},
    }
    coverage = {
        canonical(s1): {"confidence": 0.9, "choice": "step_a", "samples": 5},
        canonical(s2): {"confidence": 0.9, "choice": "step_a", "samples": 5},
    }
    payload = {
        "choices": choices,
        "coverage": coverage,
        "engine_data": {"engine": "exact", "table": table},
    }

    # Task T1: Step 1 succeeded (1.0), but Step 2 had MISSING outcome
    # Task T2: Step 1 succeeded (1.0), but Step 2 had UNVERIFIED DISAGREEMENT
    rows = [
        # Task T1
        {
            "id": "t1-s1",
            "task": "T1",
            "state": s1,
            "choice": "step_a",
            "outcome": {"quality": 1.0, "verifier": "obs", "verifier_version": "1", "evidence": {}},
        },
        {
            "id": "t1-s2",
            "task": "T1",
            "state": s2,
            "choice": "step_a",
            "outcome": None,  # Missing outcome in step 2
        },
        # Task T2
        {
            "id": "t2-s1",
            "task": "T2",
            "state": s1,
            "choice": "step_a",
            "outcome": {"quality": 1.0, "verifier": "obs", "verifier_version": "1", "evidence": {}},
        },
        {
            "id": "t2-s2",
            "task": "T2",
            "state": s2,
            "choice": "step_b",  # Host chose step_b, engine chose step_a -> disagreement
            "outcome": {"quality": 1.0, "verifier": "obs", "verifier_version": "1", "evidence": {}},
        },
    ]

    results = verify_rows(rows, engine, payload, verifier=None)
    assert len(results) == 4

    stats = statistics(results)
    # Total tasks = 2 (T1 and T2)
    assert stats["samples"] == 2
    # Neither T1 nor T2 can be counted as observed_positive!
    assert stats["observed_positive"] == 0
    assert stats["unknown"] == 2
    assert stats["quality_wilson_lower"] == 0.0

