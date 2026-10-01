"""Tests for Decision Site Economics profiler and advisory recommendations."""

from __future__ import annotations

import time

from microloop import DecisionSite, FallbackResult, Microloop
from microloop.internal.profiler import profile_history

SITE = DecisionSite(
    name="test.economics",
    state_schema={"query": "string"},
    choices=("allow", "deny"),
    fallback_revision="1",
)


def _make_rows(
    count: int,
    *,
    num_unique: int = 5,
    outcomes_pct: float = 1.0,
    volatility: float = 0.0,
    elapsed_p50: float = 0.15,
    cost_per_decision: float = 0.002,
):
    rows = []
    unique_states = [{"query": f"test_query_{i}"} for i in range(num_unique)]
    now = time.time()
    for i in range(count):
        st = unique_states[i % num_unique]
        ch = "allow"
        if volatility > 0 and (i % 2 == 1):
            ch = "deny"
        has_out = (i / count) < outcomes_pct
        out = {"quality": 1} if has_out else None
        rows.append(
            {
                "task": f"task_{i}",
                "state": st,
                "choice": ch,
                "source": "fallback",
                "reason": "cold_start",
                "outcome": out,
                "created": now + i,
                "elapsed": elapsed_p50,
                "usage": {"cost": cost_per_decision, "model_calls": 1},
            }
        )
    return rows


def test_profiler_needs_more_data():
    rows = _make_rows(30)
    prof = profile_history(rows)
    assert prof.recommendation == "needs_more_data"
    assert prof["recommendation"] == "needs_more_data"
    assert any("Insufficient observations" in r for r in prof.recommendation_reasons)


def test_profiler_poor_repetition():
    rows = _make_rows(100, num_unique=95)
    prof = profile_history(rows)
    assert prof.recommendation == "poor_repetition"
    assert prof.unique_state_ratio > 0.85
    assert any("High state entropy" in r for r in prof.recommendation_reasons)


def test_profiler_weak_verifier():
    rows = _make_rows(100, num_unique=5, outcomes_pct=0.60)
    prof = profile_history(rows)
    assert prof.recommendation == "weak_verifier"
    assert prof.outcome_completeness < 0.80
    assert any("Outcome feedback completeness" in r for r in prof.recommendation_reasons)


def test_profiler_high_volatility():
    rows = _make_rows(100, num_unique=5, outcomes_pct=1.0, volatility=0.50)
    prof = profile_history(rows)
    assert prof.recommendation == "high_volatility"
    assert prof.policy_volatility > 0.15
    assert any("Policy volatility" in r for r in prof.recommendation_reasons)


def test_profiler_low_economic_value():
    rows = _make_rows(
        100,
        num_unique=5,
        outcomes_pct=1.0,
        volatility=0.0,
        elapsed_p50=0.002,
        cost_per_decision=0.00001,
    )
    prof = profile_history(rows)
    assert prof.recommendation == "low_economic_value"
    assert prof.fallback_p50_ms < 10.0
    assert prof.fallback_cost_per_decision < 0.0001
    assert any("yield negligible savings" in r for r in prof.recommendation_reasons)


def test_profiler_strong_candidate():
    rows = _make_rows(
        100,
        num_unique=5,
        outcomes_pct=1.0,
        volatility=0.0,
        elapsed_p50=0.15,
        cost_per_decision=0.002,
    )
    prof = profile_history(rows)
    assert prof.recommendation == "strong_candidate"
    assert prof.exact_repeat_rate >= 0.80
    assert prof.outcome_completeness == 1.0
    assert prof.break_even_decisions < 200
    assert any("High repetition" in r for r in prof.recommendation_reasons)


def test_profiler_cannot_grant_serving_authority(tmp_path):
    path = tmp_path / "microloop.db"
    with Microloop(path) as client:
        for i in range(60):
            res = client.decide(
                site=SITE,
                state={"query": f"item_{i % 3}"},
                fallback=lambda: FallbackResult("allow", cost=0.002, model_calls=1),
            )
            client.record_outcome(
                res.decision_id,
                quality=1,
                verifier="test",
                verifier_version="1",
                evidence={"ok": True},
            )
        prof = client.profile(SITE)
        assert prof.recommendation == "strong_candidate"
        # Profiler gives advisory recommendation, but does NOT compile or activate fast paths
        insp = client.inspect(SITE)
        assert insp["fast_path"] is None
        assert insp["state"] == "OBSERVE"
        # Next decision must still route to fallback until shadow qualification is performed
        nxt = client.decide(
            site=SITE,
            state={"query": "item_0"},
            fallback=lambda: "allow",
        )
        assert nxt.source == "fallback"
