"""Autonomous self-tuning, region health, multimodal splitting, hot-swap, and requalification."""

from dataclasses import asdict

import numpy as np
from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements
from ink.internal.coverage import CoverageEngine

SITE = DecisionSite("support.ticket", {"text": "string"}, ("refund", "tech"))
REQ = PromotionRequirements(
    min_samples=10,
    min_quality=0.5,
    min_confidence=0.5,
    max_degradation=0.8,
    comparison_rate=0.30,
    min_region_samples=5,
    evaluation_window=100,
    min_comparison_rate=0.05,
    allow_adaptive_comparison=True,
    allow_region_split=True,
    allow_auto_requalify=True,
)


def verify(state, choice):
    exp = "refund" if "refund" in state["text"] or "charge" in state["text"] else "tech"
    return Outcome(float(choice == exp), "verifier_p5", "1", {"expected": exp})


def seed_site(client, count, prefix="task"):
    for i in range(count):
        if i % 2 == 0:
            text = f"request duplicate charge refund {i % 4}"
            exp = "refund"
        else:
            text = f"technical error with application login {i % 4}"
            exp = "tech"
        st = {"text": text}
        res = client.decide(
            site=SITE,
            state=st,
            task_id=f"{prefix}-{i}",
            fallback=lambda exp=exp: FallbackResult(exp, model_calls=1),
        )
        out = verify(st, res.choice)
        client.record_outcome(res.decision_id, **asdict(out))


def activate(client, req=REQ):
    seed_site(client, 300, prefix="obs")
    client.compile(SITE, engine="exact")
    client.calibrate(SITE, verifier=verify, requirements=req)
    seed_site(client, 100, prefix="shadow")
    eval_res = client.evaluate(SITE, verifier=verify)
    assert eval_res["qualified"]


def test_region_health_model(tmp_path):
    with Ink(tmp_path / "health.db") as client:
        activate(client)
        seed_site(client, 50, prefix="active")

        regions = client.all_region_health(SITE)
        assert len(regions) >= 1
        h = regions[0]
        assert "region_id" in h
        assert h["status"] in ("ACTIVE", "SHADOW")
        assert h["sample_count"] > 0
        assert h["verified_quality"] >= 0.0
        assert h["quality_lower_bound"] >= 0.0
        assert 0.0 <= h["comparison_disagreement_rate"] <= 1.0
        assert h["distance_mean"] >= 0.0
        assert h["distance_p95"] >= 0.0
        assert h["negative_margin"] > 0.0
        assert h["radius"] > 0.0
        assert h["outcome_completeness"] >= 0.0


def test_adaptive_comparison_and_escalation(tmp_path):
    with Ink(tmp_path / "adaptive.db") as client:
        activate(client)
        art = client._artifact(SITE.version)
        prof = art["profile"]

        rate_initial = client._effective_comparison_rate(art, prof)
        assert rate_initial == REQ.comparison_rate

        # Seed 150 successful fast path decisions to allow decay
        for i in range(150):
            st = {"text": f"request duplicate charge refund {i % 4}"}
            res = client.decide(site=SITE, state=st, fallback=lambda: "refund")
            out = verify(st, res.choice)
            client.record_outcome(res.decision_id, **asdict(out))

        rate_decayed = client._effective_comparison_rate(art, prof)
        assert rate_decayed < rate_initial
        assert rate_decayed >= REQ.min_comparison_rate

        # Inject drift to trigger escalation
        client.reevaluate(SITE)
        rate_escalated = client._effective_comparison_rate(art, prof)
        assert rate_escalated >= rate_decayed


def test_high_risk_policy_locks_adaptation(tmp_path):
    high_risk_req = PromotionRequirements(
        min_samples=10,
        min_quality=0.5,
        min_confidence=0.5,
        max_degradation=0.8,
        comparison_rate=0.30,
        min_region_samples=5,
        evaluation_window=100,
        high_risk=True,
    )
    assert not high_risk_req.allow_adaptive_comparison
    assert not high_risk_req.allow_region_split
    assert not high_risk_req.allow_auto_requalify

    with Ink(tmp_path / "high_risk.db") as client:
        activate(client, req=high_risk_req)
        art = client._artifact(SITE.version)
        prof = art["profile"]
        rate = client._effective_comparison_rate(art, prof)
        assert rate == high_risk_req.comparison_rate


def test_multimodal_region_split_lifecycle(tmp_path):
    with Ink(tmp_path / "split.db") as client:
        activate(client)
        art = client._artifact(SITE.version)
        cov_engine = CoverageEngine.from_dict(art["profile"]["coverage_engine"])
        reg = cov_engine.semantic_regions[0]

        # Synthesize bimodal member vectors: cluster A vs cluster B
        dim = len(reg.prototype_vector)
        vec_a = np.zeros(dim, dtype=np.float32)
        vec_a[0 : max(1, dim // 2)] = 1.0
        vec_a /= np.linalg.norm(vec_a)

        vec_b = np.zeros(dim, dtype=np.float32)
        vec_b[max(1, dim // 2) :] = 1.0
        vec_b /= np.linalg.norm(vec_b)

        vecs = [vec_a + np.random.normal(0, 0.01, dim).astype(np.float32) for _ in range(15)]
        vecs += [vec_b + np.random.normal(0, 0.01, dim).astype(np.float32) for _ in range(15)]
        for v in vecs:
            v /= np.linalg.norm(v)

        states = [{"text": f"query_{i}"} for i in range(30)]
        split = reg.detect_multimodal_split(vecs, states, min_region_samples=5)
        assert split is not None
        child_a, child_b = split
        assert child_a.status == "SHADOW"
        assert child_b.status == "SHADOW"
        assert child_a.region_id.startswith(f"{reg.region_id}.")
        assert child_b.region_id.startswith(f"{reg.region_id}.")
        assert child_a.radius < reg.radius
        assert child_b.radius < reg.radius


def test_zero_downtime_hot_swap(tmp_path):
    with Ink(tmp_path / "hotswap.db") as client:
        activate(client)
        art_old = client._artifact(SITE.version)
        old_id = art_old["id"]

        # Compile a replacement candidate
        new_id = client.compile(SITE, engine="exact", replace_existing=True)
        client.calibrate(SITE, verifier=verify, requirements=REQ)
        seed_site(client, 100, prefix="new_shadow")
        eval_res = client.evaluate(SITE, verifier=verify, auto_promote=False)
        assert eval_res["qualified"]

        swapped_id = client.hot_swap(SITE, new_id)
        assert swapped_id == new_id

        art_active = client._artifact(SITE.version)
        assert art_active["id"] == new_id
        assert art_active["status"] == "ACTIVE"

        links = client.lineage(SITE)
        assert any(link["parent"] == old_id and link["child"] == new_id for link in links)


def test_autonomous_requalification_after_drift(tmp_path):
    with Ink(tmp_path / "requalify.db") as client:
        activate(client)
        art = client._artifact(SITE.version)

        # Force demotion to SHADOW by recording bad outcomes
        for i in range(100):
            st = {"text": f"request duplicate charge refund {i % 4}"}
            res = client.decide(site=SITE, state=st, fallback=lambda: "refund")
            client.record_outcome(
                res.decision_id,
                quality=0.0,
                verifier="verifier_p5",
                verifier_version="1",
                evidence={"drift": True},
            )

        reeval = client.reevaluate(SITE)
        assert reeval["demoted"] is True
        assert client._artifact(SITE.version)["status"] == "SHADOW"

        # Now feed fresh post-demotion fallback observations with good outcomes
        seed_site(client, 120, prefix="fresh_post_drift")

        # Run maintenance to trigger autonomous requalification
        maint = client.maintenance(verifier=verify, requirements=REQ)
        assert maint[SITE.name].get("qualified") is True
        assert client._artifact(SITE.version)["status"] == "ACTIVE"
        events = client.store.rows(
            "SELECT * FROM events WHERE artifact=? AND current='ACTIVE'",
            (art["id"],),
        )
        assert len(events) >= 2
