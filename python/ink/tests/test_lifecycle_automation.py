import time
from dataclasses import asdict

import pytest
from ink import (
    DecisionSite,
    FallbackResult,
    Ink,
    Outcome,
    PromotionRequirements,
)

REQ = PromotionRequirements(
    min_samples=6,
    min_quality=0.5,
    min_confidence=0.5,
    max_degradation=0.8,
    comparison_rate=0.25,
    min_region_samples=3,
    evaluation_window=50,
)


def _feed_traffic(
    loop,
    site,
    count,
    prefix="t",
    quality=1.0,
    state_fn=None,
    choice_fn=None,
):
    results = []
    for i in range(count):
        st = state_fn(i) if state_fn else {"tier": i % 2}
        expected = choice_fn(st) if choice_fn else ("approve" if st.get("tier", 0) == 0 else "deny")
        res = loop.decide(
            site=site,
            state=st,
            task_id=f"{prefix}-{i}",
            fallback=lambda exp=expected: FallbackResult(exp, model_calls=1),
        )
        q = quality if quality is not None else (1.0 if res.choice == expected else 0.0)
        outcome = Outcome(q, "verifier_v1", "1", {"eval": True})
        loop.record_outcome(res.decision_id, **asdict(outcome))
        results.append(res)
    return results


def test_autonomous_lifecycle_full_loop(tmp_path):
    db_path = str(tmp_path / "auto_life.db")
    site = DecisionSite("auto.service", {"tier": "integer"}, ("approve", "deny"))

    with Ink(
        db_path,
        auto_maintenance=True,
        maintenance_interval=0.05,
        maintenance_requirements=REQ,
        maintenance_engine="exact",
    ) as loop:
        loop.register(site)
        assert loop.status(site)["state"] == "OBSERVE"

        # 1. OBSERVE: Feed 100 observations to satisfy partition and bound requirements
        _feed_traffic(loop, site, 100, "obs", quality=1.0)

        # Wait for background thread to detect compile eligibility and advance to SHADOW
        deadline = time.time() + 4.0
        while time.time() < deadline:
            st = loop.status(site)
            if st["state"] == "SHADOW":
                break
            time.sleep(0.05)
        assert loop.status(site)["state"] == "SHADOW"

        # 2. SHADOW: Feed fresh shadow traffic
        _feed_traffic(loop, site, 30, "shadow", quality=1.0)

        # Wait for background thread to evaluate shadow evidence and advance to ACTIVE
        deadline = time.time() + 4.0
        while time.time() < deadline:
            st = loop.status(site)
            if st["state"] == "ACTIVE":
                break
            time.sleep(0.05)
        assert loop.status(site)["state"] == "ACTIVE"
        assert loop.status(site)["active_candidate"] is not None

        # 3. ACTIVE: Verify fast path serves traffic
        active_res = _feed_traffic(loop, site, 20, "act", quality=1.0)
        assert any(r.source == "fast_path" for r in active_res)

        # 4. DRIFT: Inject degraded outcomes until automatic demotion triggers
        for i in range(40):
            res = loop.decide(
                site=site,
                state={"tier": i % 2},
                task_id=f"drift-{i}",
                fallback=lambda: FallbackResult("approve", model_calls=1),
            )
            loop.record_outcome(
                res.decision_id,
                quality=0.0,
                verifier="verifier_v1",
                verifier_version="1",
                evidence={"eval": True},
            )
            if loop.status(site)["state"] == "SHADOW":
                break
            time.sleep(0.02)
        assert loop.status(site)["state"] == "SHADOW"

        # Decisions during demotion fall back to model
        demoted_res = loop.decide(
            site=site,
            state={"tier": 0},
            fallback=lambda: FallbackResult("approve", model_calls=1),
        )
        assert demoted_res.source == "fallback"
        loop.record_outcome(
            demoted_res.decision_id,
            quality=1.0,
            verifier="verifier_v1",
            verifier_version="1",
            evidence={"eval": True},
        )

        # 5. REQUALIFY: Feed fresh healthy shadow traffic to requalify
        _feed_traffic(loop, site, 30, "requal", quality=1.0)
        deadline = time.time() + 4.0
        while time.time() < deadline:
            st = loop.status(site)
            if st["state"] == "ACTIVE":
                break
            time.sleep(0.05)
        st_final = loop.status(site)
        assert st_final["state"] == "ACTIVE"


def test_maintenance_coordinator_thread_lifecycle(tmp_path):
    db_path = str(tmp_path / "thread_mgr.db")

    # auto_maintenance=False must not launch any thread
    with Ink(db_path, auto_maintenance=False) as loop:
        assert loop._maint_thread is None

    # auto_maintenance=True starts daemon thread
    loop = Ink(db_path, auto_maintenance=True, maintenance_interval=0.1)
    try:
        assert loop._maint_thread is not None
        assert loop._maint_thread.is_alive()
        assert loop._maint_thread.daemon is True
    finally:
        loop.close()
        assert loop._maint_thread is None

    # Context manager cleanly exits without thread hang
    with Ink(db_path, auto_maintenance=True, maintenance_interval=0.1) as loop2:
        t = loop2._maint_thread
        assert t.is_alive()
    assert not t.is_alive()


def test_foreground_latency_isolation(tmp_path):
    db_path = str(tmp_path / "latency.db")
    site = DecisionSite("latency.site", {"tier": "integer"}, ("approve", "deny"))

    with Ink(
        db_path,
        auto_maintenance=True,
        maintenance_interval=0.01,
        maintenance_requirements=REQ,
        maintenance_engine="exact",
    ) as loop:
        loop.register(site)
        _feed_traffic(loop, site, 100, "seed", quality=1.0)

        # Measure foreground decide latencies under active background maintenance
        lats = []
        for i in range(50):
            t0 = time.perf_counter()
            loop.decide(
                site=site,
                state={"tier": i % 2},
                fallback=lambda: FallbackResult("approve", model_calls=1),
            )
            lats.append(time.perf_counter() - t0)

        lats.sort()
        median_lat = lats[len(lats) // 2]
        assert median_lat < 0.01


def test_narrowed_exception_boundary_preserves_programmer_errors(tmp_path):
    db_path = str(tmp_path / "exc_boundary.db")
    site = DecisionSite("contract.test", {"tier": "integer"}, ("approve", "deny"))

    with Ink(db_path) as loop:
        loop.register(site)

        # Non-dict state must raise TypeError, not fail open silently
        with pytest.raises(TypeError, match="State must be a dict"):
            loop.decide(
                site=site,
                state="not_a_dict",
                fallback=lambda: "approve",
            )

        # Undeclared field in state must raise ValueError
        with pytest.raises(ValueError, match="only declared fields"):
            loop.decide(
                site=site,
                state={"tier": 1, "extra": "invalid"},
                fallback=lambda: "approve",
            )

        # Fallback returning undeclared choice must raise ValueError
        with pytest.raises(ValueError, match="undeclared choice"):
            loop.decide(
                site=site,
                state={"tier": 1},
                fallback=lambda: "unknown_choice",
            )


def test_explainability_and_blockers(tmp_path):
    db_path = str(tmp_path / "explain.db")
    site = DecisionSite("explain.site", {"tier": "integer"}, ("approve", "deny"))

    with Ink(db_path, maintenance_requirements=REQ) as loop:
        loop.register(site)

        # 0 observations -> waiting_for_observations
        insp = loop.inspect(site)
        assert insp["state"] == "OBSERVE"
        assert insp["blocker"] == "waiting_for_observations"
        assert loop.status(site)["blocker"] == "waiting_for_observations"

        # Decisions with no outcomes -> waiting_for_outcomes
        for i in range(10):
            loop.decide(
                site=site,
                state={"tier": i % 2},
                fallback=lambda: FallbackResult("approve", model_calls=1),
            )
        assert loop.status(site)["blocker"] == "waiting_for_outcomes"

        # Single task group for all outcomes -> insufficient_task_groups
        for row in loop.store.rows("SELECT id FROM decisions"):
            loop.record_outcome(
                row["id"],
                quality=1.0,
                verifier="test",
                verifier_version="1",
                evidence={"ok": True},
            )
        with loop.store.transaction() as db:
            db.execute("UPDATE decisions SET task='single_task'")
        assert loop.status(site)["blocker"] == "insufficient_task_groups"


def test_exact_factual_qualification_without_counterfactual_verifier(tmp_path):
    db_path = str(tmp_path / "exact_qual.db")
    site = DecisionSite("exact.qual", {"tier": "integer"}, ("approve", "deny"))

    with Ink(db_path, maintenance_requirements=REQ) as loop:
        loop.register(site)
        _feed_traffic(loop, site, 100, "seed", quality=1.0)

        # Compile exact engine candidate
        loop.compile(site, engine="exact")
        assert loop.status(site)["state"] == "SHADOW"

        # Calibrate with verifier=None using factual outcomes
        prof = loop.calibrate(site, verifier=None, requirements=REQ)
        assert prof is not None

        # Feed fresh shadow traffic with factual outcomes
        _feed_traffic(loop, site, 30, "shadow", quality=1.0)

        # Evaluate with verifier=None using factual outcomes
        ev = loop.evaluate(site, verifier=None)
        assert ev["qualified"] is True
        assert loop.status(site)["state"] == "ACTIVE"

        # Semantic engine without verifier must be rejected
        loop.compile(site, engine="decision", replace_existing=True)
        with pytest.raises(ValueError, match="Semantic engines require a verifier"):
            loop.calibrate(site, verifier=None, requirements=REQ)


def test_multi_site_fairness_and_time_budgeting(tmp_path):
    db_path = str(tmp_path / "multi_fairness.db")
    s1 = DecisionSite("site.one", {"k": "integer"}, ("a", "b"))
    s2 = DecisionSite("site.two", {"k": "integer"}, ("a", "b"))
    s3 = DecisionSite("site.three", {"k": "integer"}, ("a", "b"))

    with Ink(db_path, maintenance_requirements=REQ) as loop:
        for s in (s1, s2, s3):
            loop.register(s)
            _feed_traffic(
                loop,
                s,
                100,
                f"seed-{s.name}",
                quality=1.0,
                state_fn=lambda i: {"k": i % 2},
                choice_fn=lambda st: "a" if st["k"] == 0 else "b",
            )

        # max_sites=1 processes only 1 site and defers the rest
        res = loop.maintenance(max_sites=1, engine="exact")
        processed = [k for k, v in res.items() if "compiled" in v or "pending" in v]
        deferred = [k for k, v in res.items() if v.get("deferred") == "max_sites_reached"]
        assert len(processed) == 1
        assert len(deferred) == 2

        # time_budget_sec=0.000001 defers subsequent sites
        res2 = loop.maintenance(time_budget_sec=0.000001, engine="exact")
        assert any(v.get("deferred") == "time_budget_exceeded" for v in res2.values())
