"""Task 4: shadow verification, promotion, and re-evaluation end to end."""

from dataclasses import asdict

import pytest
from microloop import DecisionSite, FallbackResult, Microloop, Outcome, PromotionRequirements

SITE = DecisionSite("task4.lifecycle", {"refund": "boolean"}, ("refund", "specialist"))
REQ = PromotionRequirements(10, 0.5, 0.5, 0.6, 0.25, 5, 100)


def verify(state, choice):
    expected = "refund" if state["refund"] else "specialist"
    return Outcome(float(choice == expected), "ledger", "1", {"expected": expected})


def run(client, count, prefix="task", bad=False, record=True):
    results = []
    for i in range(count):
        state = {"refund": i % 2 == 0}
        result = client.decide(
            site=SITE,
            state=state,
            task_id=f"{prefix}-{i}",
            fallback=lambda state=state: FallbackResult(
                "refund" if state["refund"] else "specialist", model_calls=1
            ),
        )
        if record:
            outcome = verify(state, result.choice)
            if bad:
                outcome = Outcome(0.0, "ledger", "1", {"expected": "refund"})
            client.record_outcome(result.decision_id, **asdict(outcome))
        results.append(result)
    return results


def test_maintenance_full_cycle_compile_promote_demote():
    with Microloop(":memory:") as client:
        run(client, 300, "observe")
        first = client.maintenance(verifier=verify, requirements=REQ, engine="exact")
        assert "compiled" in first[SITE.name]
        assert client.inspect(SITE)["state"] == "SHADOW"
        run(client, 100, "shadow")
        second = client.maintenance(verifier=verify, requirements=REQ, engine="exact")
        assert second[SITE.name]["qualified"]
        assert client.inspect(SITE)["state"] == "ACTIVE"
        # Active service mixes fast paths with comparison fallbacks.
        served = run(client, 40, "active")
        assert any(r.source == "fast_path" for r in served)
        assert any(r.fallback_reason == "comparison" for r in served)
        # Deliberate drift returns the path to shadow via maintenance alone.
        run(client, 100, "drift", bad=True)
        third = client.maintenance(engine="exact")
        assert third[SITE.name]["demoted"]
        assert client.inspect(SITE)["state"] == "SHADOW"
        assert run(client, 1, "after")[0].source == "fallback"


def test_shadow_runs_prediction_beside_fallback():
    with Microloop(":memory:") as client:
        run(client, 300, "observe")
        client.compile(SITE, engine="exact")
        result = run(client, 1, "shadow-one")[0]
        assert result.source == "fallback"
        assert result.fallback_reason == "shadow"
        row = client.store.history(SITE.version)[-1]
        assert row["source"] == "fallback"
        assert row["prediction"] is not None
        assert row["prediction"]["choice"] == result.choice
        assert row["choice"] == result.choice


def test_no_activation_without_outcomes_via_maintenance():
    with Microloop(":memory:") as client:
        run(client, 300, "observe", record=False)
        outcome = client.maintenance(verifier=verify, requirements=REQ, engine="exact")
        assert "pending" in outcome[SITE.name]
        assert client.inspect(SITE)["state"] == "OBSERVE"
        assert client.inspect(SITE)["outcome_completeness"] == 0


def test_promotion_guards_and_fresh_requalification():
    with Microloop(":memory:") as client:
        run(client, 300, "observe")
        client.compile(SITE, engine="exact")
        client.calibrate(SITE, verifier=verify, requirements=REQ)
        run(client, 100, "shadow")
        assert client.evaluate(SITE, verifier=verify)["qualified"]
        assert client.inspect(SITE)["state"] == "ACTIVE"
        # Second promotion attempt is rejected; ACTIVE is not a shadow candidate.
        with pytest.raises(ValueError, match="calibrated shadow"):
            client.evaluate(SITE, verifier=verify)
        with pytest.raises(ValueError, match="uncalibrated shadow"):
            client.calibrate(SITE, verifier=verify, requirements=REQ)
        # Drift demotes; the old evidence cannot requalify without fresh shadow.
        run(client, 100, "drift", bad=True)
        assert client.reevaluate(SITE)["demoted"]
        with pytest.raises(ValueError, match="fresh shadow"):
            client.evaluate(SITE, verifier=verify)


def test_partial_missing_outcomes_block_promotion():
    with Microloop(":memory:") as client:
        run(client, 300, "observe")
        client.compile(SITE, engine="exact")
        client.calibrate(SITE, verifier=verify, requirements=REQ)
        run(client, 100, "shadow")
        run(client, 1, "missing", record=False)
        with pytest.raises(ValueError, match="Missing fresh shadow"):
            client.evaluate(SITE, verifier=verify)


def test_changed_verifier_demotes_even_when_all_scores_pass():
    with Microloop(":memory:") as client:
        run(client, 300, "observe")
        client.compile(SITE, engine="exact")
        client.calibrate(SITE, verifier=verify, requirements=REQ)
        run(client, 100, "shadow")
        assert client.evaluate(SITE, verifier=verify)["qualified"]
        result = run(client, 1, "changed", record=False)[0]
        client.record_outcome(
            result.decision_id,
            quality=1.0,
            verifier="different",
            verifier_version="2",
            evidence={"ok": True},
        )
        assert client.reevaluate(SITE)["demoted"]


def test_maintenance_waits_for_compilation_support():
    with Microloop(":memory:") as client:
        run(client, 10, "early")
        result = client.maintenance(verifier=verify, requirements=REQ, engine="exact")
        assert "pending" in result[SITE.name]
        assert client.inspect(SITE)["state"] == "OBSERVE"
        run(client, 300, "ready")
        assert (
            "compiled"
            in client.maintenance(verifier=verify, requirements=REQ, engine="exact")[SITE.name]
        )


def test_early_candidate_can_be_replaced_when_evidence_arrives():
    with Microloop(":memory:") as client:
        run(client, 10, "early")
        first = client.compile(SITE, engine="exact")
        run(client, 300, "enough")
        result = client.maintenance(verifier=verify, requirements=REQ, engine="exact")
        assert "pending" in result[SITE.name]  # Replacement still needs fresh shadow traffic.
        assert client.inspect(SITE)["fast_path"] != first
        assert client.inspect(SITE)["profile"] is not None
        run(client, 100, "fresh")
        assert client.maintenance(verifier=verify, requirements=REQ, engine="exact")[SITE.name][
            "qualified"
        ]
