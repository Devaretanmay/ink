"""Task 2 hardening: queryable v4 tables (coverage, promotions, drift, lineage)."""

import sqlite3
from dataclasses import asdict

from microloop import DecisionSite, FallbackResult, Microloop, Outcome, PromotionRequirements
from microloop.internal.decision_store import SCHEMA_VERSION, DecisionStore

SITE = DecisionSite("v4.coverage", {"refund": "boolean"}, ("refund", "specialist"))
REQ = PromotionRequirements(10, 0.5, 0.5, 0.6, 0.25, 5, 100)


def verify(state, choice):
    expected = "refund" if state["refund"] else "specialist"
    return Outcome(float(choice == expected), "ledger", "1", {"expected": expected})


def run(client, count, prefix="task", bad=False):
    for i in range(count):
        state = {"refund": i % 2 == 0}
        result = client.decide(
            site=SITE, state=state, task_id=f"{prefix}-{i}",
            fallback=lambda state=state: FallbackResult(
                "refund" if state["refund"] else "specialist", model_calls=1
            ),
        )
        outcome = verify(state, result.choice)
        if bad:
            outcome = Outcome(0.0, "ledger", "1", {"expected": "refund"})
        client.record_outcome(result.decision_id, **asdict(outcome))


def test_coverage_counters_live_and_outcome_updates():
    with Microloop(":memory:") as client:
        run(client, 10, "cov")
        rows = client.coverage(SITE)
        assert len(rows) == 2
        assert sum(r["observations"] for r in rows) == 10
        assert sum(r["outcomes"] for r in rows) == 10
        assert sum(r["quality_sum"] for r in rows) == 10.0
        assert sum(r["fast_served"] for r in rows) == 0


def test_promotion_drift_lineage_tables():
    with Microloop(":memory:") as client:
        run(client, 300, "observe")
        first = client.compile(SITE)
        run(client, 20, "more")
        second = client.compile(SITE, replace_existing=True)
        assert client.lineage(SITE) == [
            {"parent": first, "child": second, "created": client.lineage(SITE)[0]["created"]}
        ]
        client.calibrate(SITE, verifier=verify, requirements=REQ)
        run(client, 100, "shadow")
        assert client.evaluate(SITE, verifier=verify)["qualified"]
        promos = client.promotions(SITE)
        assert len(promos) == 1 and promos[0]["qualified"] == 1
        assert promos[0]["holdout_samples"] >= 10 and promos[0]["shadow_samples"] >= 10
        run(client, 100, "active")
        before = len(client.drift_history(SITE))
        assert client.reevaluate(SITE)["demoted"] is False
        assert len(client.drift_history(SITE)) == before + 1
        run(client, 100, "drift", bad=True)
        assert client.reevaluate(SITE)["demoted"]
        assert client.drift_history(SITE)[0]["demoted"] == 1


def test_migration_backfills_v4_tables(tmp_path):
    import time as _time

    from microloop.internal import decision_store as ds

    path = tmp_path / "v3.db"
    conn = sqlite3.connect(str(path))
    for statement in ds.SCHEMA:
        if statement.startswith("CREATE TABLE state_coverage"):
            continue
        if statement.startswith("CREATE TABLE promotion_records"):
            continue
        if statement.startswith("CREATE TABLE drift_checks"):
            continue
        if statement.startswith("CREATE TABLE artifact_links"):
            continue
        conn.execute(statement)
    now = _time.time()
    conn.execute("INSERT INTO sites VALUES (?,?,?,?)", ("sv", "v4.coverage", "{}", now))
    conn.execute(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("d1", "sv", "t1", now, '{"refund":true}', "refund", "fast_path", None,
         "a1", '{"choice":"refund"}', 0.9, 0.01, "{}"),
    )
    conn.execute(
        "INSERT INTO outcomes VALUES (?,?,?)",
        ("d1", '{"quality": 1.0, "verifier": "ledger", "verifier_version": "1",'
               ' "evidence": {"i": 1}}', now),
    )
    conn.execute(
        "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
        ("a1", "sv", '{"engine_data": {"engine": "exact"}}', "chk", "ACTIVE", now, None,
         '{"qualified": true, "holdout": {"samples": 12, "quality_lower": 0.9,'
         ' "agreement": 1.0}, "shadow": {"samples": 12, "delta_lower": -0.1}}'),
    )
    conn.execute(
        "INSERT INTO events VALUES (?,?,?,?,?,?)",
        (1, "a1", now, "ACTIVE", "SHADOW",
         '{"active_samples": 12, "comparison_samples": 12, "missing_outcomes": 0,'
         ' "quality_lower": 0.9, "delta_lower": -0.1, "uncovered_rate": 0.0}'),
    )
    conn.execute("PRAGMA user_version=3")
    conn.commit()
    conn.close()
    store = DecisionStore(path)
    try:
        assert store.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
        cov = store.rows("SELECT * FROM state_coverage")
        assert cov == [{"site": "sv", "state": '{"refund":true}', "observations": 1,
                        "fast_served": 1, "outcomes": 1, "quality_sum": 1.0}]
        promo = store.rows("SELECT * FROM promotion_records")
        assert len(promo) == 1 and promo[0]["qualified"] == 1
        assert promo[0]["holdout_samples"] == 12 and promo[0]["shadow_samples"] == 12
        drift = store.rows("SELECT * FROM drift_checks")
        assert len(drift) == 1 and drift[0]["demoted"] == 1
    finally:
        store.close()
