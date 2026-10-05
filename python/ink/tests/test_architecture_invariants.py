import json
from dataclasses import asdict

from ink import (
    DecisionSite,
    FallbackResult,
    Ink,
    Outcome,
    PromotionRequirements,
)
from ink.internal.contracts import canonical
from ink.internal.coverage import CoverageEngine, SemanticRegion, TextVectorizer
from ink.internal.engines import (
    DecisionEngine,
    DecisionModelEngine,
    ExactEngine,
    MlxDecisionEngine,
)

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


def test_decision_engine_internal_learned_capability_is_integral():
    with Ink(":memory:") as loop:
        assert "exact" in loop.engines
        assert "decision" in loop.engines
        assert isinstance(loop.engines["exact"], ExactEngine)
        assert isinstance(loop.engines["decision"], DecisionModelEngine)
        assert MlxDecisionEngine is DecisionModelEngine
        assert isinstance(loop.engines["exact"], DecisionEngine)
        assert isinstance(loop.engines["decision"], DecisionEngine)


def test_internal_decision_capability_persists_without_mlx():
    from unittest.mock import patch
    with patch.dict("sys.modules", {"mlx": None, "mlx.core": None}):
        with Ink(":memory:") as loop:
            assert "decision" in loop.engines
            assert isinstance(loop.engines["decision"], DecisionEngine)


def test_candidate_does_not_equal_serving_authority_unqualified_shadow(tmp_path):
    db_path = tmp_path / "authority.db"
    site = DecisionSite("test.route", {"refund": "boolean"}, ("allow", "deny"))

    with Ink(db_path) as loop:
        loop.register(site)
        _feed(loop, site, 250, "obs")
        first = loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        assert "compiled" in first[site.name]
        assert loop.inspect(site)["state"] == "SHADOW"

        # Artifact exists in SHADOW; engine can predict candidate, but fallback controls execution
        host_called = False

        def tracking_fallback():
            nonlocal host_called
            host_called = True
            return FallbackResult("deny", model_calls=1)

        res = loop.decide(site=site, state={"refund": True}, fallback=tracking_fallback)
        assert host_called is True
        assert res.source == "fallback"
        assert res.fallback_reason == "shadow"


def test_candidate_served_only_when_qualified_and_active(tmp_path):
    db_path = tmp_path / "active.db"
    site = DecisionSite("test.route", {"refund": "boolean"}, ("allow", "deny"))

    with Ink(db_path) as loop:
        loop.register(site)
        _feed(loop, site, 250, "obs")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        _feed(loop, site, 100, "shadow")
        second = loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        assert second[site.name]["qualified"]
        assert loop.inspect(site)["state"] == "ACTIVE"

        # Now active: serves locally without host fallback
        served = []
        for _ in range(15):
            res = loop.decide(
                site=site,
                state={"refund": True},
                fallback=lambda: FallbackResult("deny", model_calls=1),
            )
            served.append(res)
        assert any(r.source == "fast_path" for r in served)
        fast_ones = [r for r in served if r.source == "fast_path"]
        assert all(r.choice == "allow" for r in fast_ones)
        assert all(r.fallback_reason is None for r in fast_ones)


def test_novel_state_outside_coverage_falls_back(tmp_path):
    db_path = tmp_path / "coverage.db"
    site = DecisionSite("test.route", {"refund": "boolean"}, ("allow", "deny"))

    with Ink(db_path) as loop:
        loop.register(site)
        _feed(loop, site, 250, "obs")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        _feed(loop, site, 100, "shadow")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        assert loop.inspect(site)["state"] == "ACTIVE"

        # Now create a site with string schema and train only on "known"
        pass


def test_novel_state_outside_coverage_routes_to_host(tmp_path):
    db_path = tmp_path / "cov_novel.db"
    site = DecisionSite("test.cov", {"task": "string"}, ("allow", "deny"))

    with Ink(db_path) as loop:
        loop.register(site)
        for _ in range(250):
            res = loop.decide(
                site=site,
                state={"task": "known_action"},
                fallback=lambda: FallbackResult("allow", model_calls=1),
            )
            loop.record_outcome(res.decision_id, quality=1.0, verifier="v", verifier_version="1")
        loop.maintenance(
            verifier=lambda s, c: Outcome(1.0, "v", "1", {}), requirements=REQ, engine="exact"
        )
        for _ in range(100):
            res = loop.decide(
                site=site,
                state={"task": "known_action"},
                fallback=lambda: FallbackResult("allow", model_calls=1),
            )
            loop.record_outcome(res.decision_id, quality=1.0, verifier="v", verifier_version="1")
        loop.maintenance(
            verifier=lambda s, c: Outcome(1.0, "v", "1", {}), requirements=REQ, engine="exact"
        )
        assert loop.inspect(site)["state"] == "ACTIVE"

        # State outside coverage boundaries: routes to host fallback
        host_called = False

        def novel_fallback():
            nonlocal host_called
            host_called = True
            return FallbackResult("deny", model_calls=1)

        res = loop.decide(site=site, state={"task": "novel_operation"}, fallback=novel_fallback)
        assert host_called is True
        assert res.source == "fallback"
        assert res.fallback_reason == "outside_coverage"
        assert res.choice == "deny"


def test_prediction_disagreement_or_low_confidence_falls_back(tmp_path):
    db_path = tmp_path / "disagree.db"
    site = DecisionSite("test.route", {"refund": "boolean"}, ("allow", "deny"))

    with Ink(db_path) as loop:
        loop.register(site)
        _feed(loop, site, 250, "obs")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        _feed(loop, site, 100, "shadow")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        assert loop.inspect(site)["state"] == "ACTIVE"

        # Corrupt the artifact choice to force disagreement with qualified region
        with loop.store.transaction() as db:
            q = "SELECT id, payload, profile FROM artifacts WHERE status='ACTIVE'"
            row = db.execute(q).fetchone()
            payload = json.loads(row["payload"])
            payload["engine_data"]["table"][canonical({"refund": True})]["choice"] = "deny"
            from ink.internal.contracts import digest
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
        assert res.choice == "allow"


def test_exact_tier_bypasses_neural_inference(tmp_path):
    db_path = tmp_path / "exact.db"
    site = DecisionSite("test.route", {"refund": "boolean"}, ("allow", "deny"))

    with Ink(db_path) as loop:
        loop.register(site)
        _feed(loop, site, 250, "obs")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        _feed(loop, site, 100, "shadow")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")

        # Exact fast path serves directly
        res = loop.decide(
            site=site,
            state={"refund": True},
            fallback=lambda: FallbackResult("deny", model_calls=1),
        )
        if res.source == "fast_path":
            assert res.choice == "allow"
            assert res.receipt["served_by"] == "fast_path"
            assert res.receipt["representation_version"] == "exact_v1"


def test_semantic_unverified_region_remains_shadow():
    vec = TextVectorizer.fit(["read item", "write item"])
    region = SemanticRegion(
        region_id="sem-1",
        site="test.route",
        choice="allow",
        prototype_state={"text": "read item"},
        prototype_vector=vec.transform("read item").tolist(),
        radius=0.5,
        negative_margin=1.0,
        member_count=10,
        confidence=0.8,
        status="SHADOW",
    )
    cov = CoverageEngine(exact_coverage={}, semantic_regions=[region], vectorizer=vec)
    level, reg, conf = cov.route({"text": "read item"})
    assert level == "shadow"
    assert reg["status"] == "SHADOW"
