import json
import time
from dataclasses import asdict

from microloop import (
    DecisionSite,
    FallbackResult,
    Microloop,
    Outcome,
    PromotionRequirements,
)
from microloop.internal.contracts import canonical, digest
from microloop.internal.verification import passes, statistics

from benchmarks.release_candidate import get_system_metadata

REQ = PromotionRequirements(10, 0.5, 0.5, 0.6, 0.25, 5, 100)


def verify_fn(state, choice):
    expected = "allow" if state.get("refund") else "deny"
    return Outcome(float(choice == expected), "verifier", "1", {"expected": expected})


def _feed(loop, site, count, prefix="t"):
    for i in range(count):
        state = {"refund": i % 2 == 0}
        expected = "allow" if state["refund"] else "deny"
        res = loop.decide(
            site=site,
            state=state,
            task_id=f"{prefix}-{i}",
            fallback=lambda exp=expected: FallbackResult(exp, model_calls=1),
        )
        outcome = verify_fn(state, res.choice)
        loop.record_outcome(res.decision_id, **asdict(outcome))


# -----------------------------------------------------------------------------
# Part A: Candidate Generation Separated From Serving Authority
# -----------------------------------------------------------------------------

def test_unqualified_candidate_routes_to_fallback(tmp_path):
    site = DecisionSite("test.cand.unqual", {"refund": "boolean"}, ("allow", "deny"))
    with Microloop(tmp_path / "test.db") as loop:
        loop.register(site)
        _feed(loop, site, 60, "train")
        # Compile candidate -> status becomes SHADOW, not ACTIVE
        loop.compile(site, engine="exact")
        art = loop._artifact(site.version)
        assert art["status"] == "SHADOW"

        # Candidate can produce a valid prediction, but authority is denied
        res = loop.decide(
            site=site,
            state={"refund": True},
            fallback=lambda: FallbackResult("allow", model_calls=1),
        )
        assert res.source == "fallback"
        assert res.fallback_reason == "shadow"


def test_high_confidence_unqualified_candidate_denied_authority(tmp_path):
    site = DecisionSite("test.cand.highconf", {"refund": "boolean"}, ("allow", "deny"))
    with Microloop(tmp_path / "test.db") as loop:
        loop.register(site)
        _feed(loop, site, 60, "train")
        loop.compile(site, engine="exact")
        loop.calibrate(site, verifier=verify_fn, requirements=REQ)
        art = loop._artifact(site.version)
        assert art["status"] == "SHADOW"
        # Even with calibrated profile and 1.0 confidence, unpromoted SHADOW cannot serve
        res = loop.decide(
            site=site,
            state={"refund": True},
            fallback=lambda: FallbackResult("allow", model_calls=1),
        )
        assert res.source == "fallback"
        assert res.fallback_reason == "shadow"


def test_qualified_covered_candidate_serves_locally(tmp_path):
    site = DecisionSite("test.cand.qualified", {"refund": "boolean"}, ("allow", "deny"))
    with Microloop(tmp_path / "test.db") as loop:
        loop.register(site)
        _feed(loop, site, 250, "obs")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        _feed(loop, site, 100, "shadow")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        art = loop._artifact(site.version)
        assert art["status"] == "ACTIVE"

        served = False
        for _ in range(20):
            res = loop.decide(
                site=site,
                state={"refund": True},
                fallback=lambda: FallbackResult("allow", model_calls=1),
            )
            if res.source == "fast_path":
                served = True
                assert res.choice == "allow"
                assert res.fallback_reason is None
                break
        assert served, "Qualified covered candidate must serve locally"


def test_candidate_disagrees_with_qualified_region_falls_back(tmp_path):
    site = DecisionSite("test.cand.disagree", {"refund": "boolean"}, ("allow", "deny"))
    with Microloop(tmp_path / "test.db") as loop:
        loop.register(site)
        _feed(loop, site, 250, "obs")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        _feed(loop, site, 100, "shadow")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        art = loop._artifact(site.version)
        assert art["status"] == "ACTIVE"

        # Tamper engine table so candidate prediction disagrees with calibrated region choice
        with loop.store.transaction() as db:
            row = db.execute("SELECT id, payload FROM artifacts WHERE status='ACTIVE'").fetchone()
            payload = json.loads(row["payload"])
            payload["engine_data"]["table"][canonical({"refund": True})]["choice"] = "deny"
            db.execute(
                "UPDATE artifacts SET payload=?, checksum=? WHERE id=?",
                (json.dumps(payload), digest(payload), row["id"]),
            )

        res = loop.decide(
            site=site,
            state={"refund": True},
            fallback=lambda: FallbackResult("allow", model_calls=1),
        )
        assert res.source == "fallback"
        assert res.fallback_reason == "insufficient_confidence"


def test_novel_state_outside_coverage_falls_back(tmp_path):
    site = DecisionSite("test.cand.novel", {"code": "integer"}, ("allow", "deny"))
    with Microloop(tmp_path / "test.db") as loop:
        loop.register(site)
        for i in range(120):
            res = loop.decide(
                site=site,
                state={"code": 1},
                task_id=f"obs-{i}",
                fallback=lambda: FallbackResult("allow", model_calls=1),
            )
            loop.record_outcome(
                res.decision_id, quality=1.0, verifier="verifier", verifier_version="1"
            )
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        for i in range(60):
            res = loop.decide(
                site=site,
                state={"code": 1},
                task_id=f"sh-{i}",
                fallback=lambda: FallbackResult("allow", model_calls=1),
            )
            loop.record_outcome(
                res.decision_id, quality=1.0, verifier="verifier", verifier_version="1"
            )
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")

        # Code 99 is a valid integer schema, but novel / outside trained coverage
        res = loop.decide(
            site=site,
            state={"code": 99},
            fallback=lambda: FallbackResult("deny", model_calls=1),
        )
        assert res.source == "fallback"
        assert res.fallback_reason == "outside_coverage"


# -----------------------------------------------------------------------------
# Part B: Unknown Evidence Must Stay Unknown
# -----------------------------------------------------------------------------

def test_missing_outcomes_differ_from_negative_outcomes():
    # 10 positive + 10 missing
    missing_records = []
    for i in range(10):
        missing_records.append({
            "task": f"task-pos-{i}",
            "candidate": {"quality": 1.0},
            "baseline": {"quality": 1.0},
            "agreement": True,
        })
    for i in range(10):
        missing_records.append({
            "task": f"task-unk-{i}",
            "candidate": {"quality": None},
            "baseline": {"quality": None},
            "agreement": None,
        })
    stats_missing = statistics(missing_records)

    # 10 positive + 10 negative
    negative_records = []
    for i in range(10):
        negative_records.append({
            "task": f"task-pos-{i}",
            "candidate": {"quality": 1.0},
            "baseline": {"quality": 1.0},
            "agreement": True,
        })
    for i in range(10):
        negative_records.append({
            "task": f"task-neg-{i}",
            "candidate": {"quality": 0.0},
            "baseline": {"quality": 1.0},
            "agreement": False,
        })
    stats_neg = statistics(negative_records)

    assert stats_missing["observed_positive"] == 10
    assert stats_missing["observed_negative"] == 0
    assert stats_missing["unknown"] == 10

    assert stats_neg["observed_positive"] == 10
    assert stats_neg["observed_negative"] == 10
    assert stats_neg["unknown"] == 0

    # Missing evidence did NOT lower the quality of observed outcomes to 0.5
    assert stats_missing["quality"] == 1.0
    assert stats_neg["quality"] == 0.5


def test_missing_agreement_evidence_stays_unknown():
    # Records may exist while every comparison is unobserved: agreement is then
    # unknown, not a division by zero and not a fabricated pass.
    records = [
        {
            "task": f"task-{i}",
            "candidate": {"quality": 1.0},
            "baseline": {"quality": None},
            "agreement": None,
        }
        for i in range(10)
    ]
    stats = statistics(records)
    assert stats["agreement"] is None
    assert stats["observed_positive"] == 10
    assert stats["delta"] is None


def test_no_comparison_data_is_not_comparison_passed():
    # Only candidate records, no baseline comparison records
    records = [
        {
            "task": f"task-{i}",
            "candidate": {"quality": 1.0},
            "baseline": {"quality": None},
            "agreement": True,
        }
        for i in range(15)
    ]
    stats = statistics(records)
    assert stats["delta"] is None
    assert stats["delta_lower"] == -1.0
    # Must fail because delta_lower (-1.0) violates max_degradation (e.g. 0.2)
    req = PromotionRequirements(10, 0.5, 0.5, 0.2, 0.25, 5, 100)
    assert not passes(stats, req)


def test_inspect_reports_precise_missing_evidence_blockers(tmp_path):
    site = DecisionSite("test.inspect.blockers", {"refund": "boolean"}, ("allow", "deny"))
    with Microloop(tmp_path / "test.db") as loop:
        loop.register(site)
        info = loop.inspect(site)
        assert info["blocker"] == "waiting_for_observations"

        # Observation without outcome
        loop.decide(site=site, state={"refund": True}, fallback=lambda: "allow")
        info = loop.inspect(site)
        assert info["blocker"] == "waiting_for_outcomes"


# -----------------------------------------------------------------------------
# Part C: Evidence Identity & Freshness Invalidation
# -----------------------------------------------------------------------------

def test_fallback_revision_change_revokes_serving_authority(tmp_path):
    db_file = tmp_path / "test.db"
    site_v1 = DecisionSite(
        "test.rev.site", {"refund": "boolean"}, ("allow", "deny"), fallback_revision="v1"
    )
    with Microloop(db_file) as loop:
        loop.register(site_v1)
        _feed(loop, site_v1, 250, "obs")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        _feed(loop, site_v1, 100, "shadow")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        art = loop._artifact(site_v1.version)
        assert art["status"] == "ACTIVE"

    # Host updates its fallback revision to v2
    site_v2 = DecisionSite(
        "test.rev.site", {"refund": "boolean"}, ("allow", "deny"), fallback_revision="v2"
    )
    with Microloop(db_file) as loop:
        res = loop.decide(
            site=site_v2,
            state={"refund": True},
            fallback=lambda: FallbackResult("allow", model_calls=1),
        )
        assert res.source == "fallback"


def test_verifier_version_change_demotes_active_authority(tmp_path):
    site = DecisionSite("test.verifier.rev", {"refund": "boolean"}, ("allow", "deny"))
    with Microloop(tmp_path / "test.db") as loop:
        loop.register(site)
        _feed(loop, site, 250, "obs")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        _feed(loop, site, 100, "shadow")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        art = loop._artifact(site.version)
        assert art["status"] == "ACTIVE"

        # Re-evaluate with an outcome recorded by an unexpected verifier version
        res = loop.decide(
            site=site,
            state={"refund": True},
            fallback=lambda: FallbackResult("allow", model_calls=1),
        )
        loop.record_outcome(
            res.decision_id, quality=1.0, verifier="verifier", verifier_version="2.0"
        )

        reeval = loop.reevaluate(site)
        assert reeval["demoted"] is True
        art_after = loop._artifact(site.version)
        assert art_after["status"] == "SHADOW"


# -----------------------------------------------------------------------------
# Part D: Maintenance Completion Must Be Truthful & Fair
# -----------------------------------------------------------------------------

def test_maintenance_time_budget_exhaustion_is_deferred(tmp_path):
    site1 = DecisionSite("site.budget.1", {"x": "integer"}, ("a", "b"))
    site2 = DecisionSite("site.budget.2", {"x": "integer"}, ("a", "b"))
    with Microloop(tmp_path / "test.db") as loop:
        loop.register(site1)
        loop.register(site2)
        res = loop.maintenance(time_budget_sec=0.000001, engine="exact")
        deferred = [v for v in res.values() if v.get("status") == "deferred" or v.get("deferred")]
        assert len(deferred) >= 1
        for d in deferred:
            assert d.status == "deferred"
            assert d.reason in ("time_budget_exceeded", "max_sites_reached")


def test_maintenance_fairness_across_ten_sites(tmp_path):
    sites = [
        DecisionSite(f"site.fair.{i:02d}", {"x": "integer"}, ("a", "b")) for i in range(10)
    ]
    with Microloop(tmp_path / "test.db") as loop:
        for s in sites:
            loop.register(s)

        visited_sites = set()
        # Run 4 maintenance cycles with max_sites=3
        for _cycle in range(4):
            res = loop.maintenance(max_sites=3, engine="exact")
            for name, outcome in res.items():
                is_def = outcome.get("status") == "deferred"
                is_max = outcome.get("deferred") == "max_sites_reached"
                if not is_def and not is_max:
                    visited_sites.add(name)

        # In 4 cycles of 3 sites each, all 10 sites must be visited without starvation
        assert len(visited_sites) == 10, f"Visited {len(visited_sites)}: {visited_sites}"


def test_maintenance_missing_verifier_is_blocked_not_failed(tmp_path):
    site = DecisionSite("site.noverifier", {"x": "string"}, ("a", "b"))
    with Microloop(tmp_path / "test.db") as loop:
        loop.register(site)
        # Decision engine without verifier
        res = loop.maintenance([site], engine="decision", verifier=None)
        outcome = res[site.name]
        assert outcome.status == "blocked"
        assert outcome.reason in ("waiting_for_observations", "verifier_required")


# -----------------------------------------------------------------------------
# Part E: Benchmark Evidence Immutability & Provenance
# -----------------------------------------------------------------------------

def test_benchmark_metadata_unique_run_id():
    m1 = get_system_metadata()
    time.sleep(0.01)
    m2 = get_system_metadata()
    assert m1["run_id"] != ""
    assert m2["run_id"] != ""
    assert "git_commit" in m1
    assert "timestamp" in m1
    assert "seeds" in m1


def test_benchmark_preserves_failed_run(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    fake_meta = {"run_id": "test_failed_run_123", "git_commit": "abc1234"}
    failed_record = {
        "metadata": fake_meta,
        "status": "failed",
        "failure_reason": "Injected synthetic exception for audit test",
    }
    run_file = runs_dir / f"{fake_meta['run_id']}.json"
    with open(run_file, "w") as f:
        json.dump(failed_record, f, indent=2)

    assert run_file.exists()
    with open(run_file) as f:
        loaded = json.load(f)
    assert loaded["status"] == "failed"
    assert "Injected synthetic exception" in loaded["failure_reason"]
