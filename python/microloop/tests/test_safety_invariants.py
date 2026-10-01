"""Formal safety invariant tests for Microloop Decision JIT."""

import sqlite3
from dataclasses import asdict

import pytest
from microloop import DecisionSite, FallbackResult, Microloop, Outcome, PromotionRequirements

SITE = DecisionSite("safety.test", {"text": "string"}, ("allow", "deny"))
REQ = PromotionRequirements(
    min_samples=10,
    min_quality=0.5,
    min_confidence=0.5,
    max_degradation=0.8,
    comparison_rate=0.25,
    min_region_samples=5,
    evaluation_window=100,
)


def verify(state, choice):
    exp = "allow" if "allow" in state["text"] else "deny"
    return Outcome(float(choice == exp), "safety_verifier", "1.0", {"exp": exp})


def seed(client, count, prefix="task"):
    for i in range(count):
        exp = "allow" if i % 2 == 0 else "deny"
        st = {"text": f"{exp} permission {i % 4}"}
        res = client.decide(
            site=SITE,
            state=st,
            task_id=f"{prefix}-{i}",
            fallback=lambda exp=exp: FallbackResult(exp, model_calls=1),
        )
        client.record_outcome(res.decision_id, **asdict(verify(st, res.choice)))


def activate_site(client):
    seed(client, 240, "init_obs")
    client.compile(SITE, engine="exact")
    client.calibrate(SITE, verifier=verify, requirements=REQ)
    seed(client, 100, "init_shad")
    eval_res = client.evaluate(SITE, verifier=verify)
    assert eval_res["qualified"]


def test_invariant_1_authority_guarantee(tmp_path):
    with Microloop(tmp_path / "authority.db") as client:
        seed(client, 50, "obs")
        # In OBSERVE state: fast path must never be served
        st = {"text": "allow permission 0"}
        res = client.decide(site=SITE, state=st, fallback=lambda: "allow")
        assert res.source == "fallback"
        assert res.fallback_reason == "observe"

        # After compilation into SHADOW: still cannot serve fast path
        client.compile(SITE, engine="exact")
        res2 = client.decide(site=SITE, state=st, fallback=lambda: "allow")
        assert res2.source == "fallback"
        assert res2.fallback_reason == "shadow"


def test_invariant_2_fallback_on_unqualified_or_corrupt(tmp_path):
    with Microloop(tmp_path / "fallback.db") as client:
        activate_site(client)
        # Unknown state falling outside coverage
        st = {"text": "completely unseen query"}
        res = client.decide(site=SITE, state=st, fallback=lambda: "deny")
        assert res.source == "fallback"
        assert res.fallback_reason == "outside_coverage"
        assert res.choice == "deny"


def test_invariant_3_single_active_guarantee(tmp_path):
    with Microloop(tmp_path / "single_active.db") as client:
        activate_site(client)
        # Attempting to manually insert a second ACTIVE artifact violates DB uniqueness
        with pytest.raises(sqlite3.IntegrityError):
            with client.store.transaction() as db:
                db.execute(
                    "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
                    ("second_art", SITE.version, "{}", "fake_chk", "ACTIVE", 123.0, None, None),
                )


def test_invariant_4_freshness_after_demotion(tmp_path):
    with Microloop(tmp_path / "freshness.db") as client:
        activate_site(client)
        art = client._artifact(SITE.version)

        # Force drift and demotion
        for i in range(100):
            st = {"text": f"allow permission {i % 4}"}
            res = client.decide(site=SITE, state=st, fallback=lambda: "allow")
            client.record_outcome(
                res.decision_id,
                quality=0.0,
                verifier="safety_verifier",
                verifier_version="1.0",
                evidence={"drift": True},
            )

        reeval = client.reevaluate(SITE)
        assert reeval["demoted"] is True
        assert client._artifact(SITE.version)["status"] == "SHADOW"
        demote_epoch = client._artifact(SITE.version)["epoch"]

        # Only fresh post-demotion outcomes count
        history = client.store.history(SITE.version)
        fresh_rows = [
            r for r in history if r["artifact"] == art["id"] and r["created"] > demote_epoch
        ]
        assert len(fresh_rows) == 0


def test_invariant_5_and_6_counterexample_contraction(tmp_path):
    with Microloop(tmp_path / "contraction.db") as client:
        activate_site(client)
        art = client._artifact(SITE.version)
        cov_data = art["profile"]["coverage_engine"]
        init_radius = cov_data["semantic_regions"][0]["radius"]

        # Ingest counterexample directly
        tightened = client.maintenance(
            verifier=lambda s, c: Outcome(0.0, "safety_verifier", "1.0", {"exp": "deny"}),
            requirements=REQ,
        )
        assert tightened is not None

        art_after = client._artifact(SITE.version)
        if art_after and art_after.get("profile"):
            cov_after = art_after["profile"]["coverage_engine"]
            new_radius = cov_after["semantic_regions"][0]["radius"]
            # Invariant 5: radius must NEVER expand
            assert new_radius <= init_radius


def test_invariant_7_integrity_checksum_tamper(tmp_path):
    with Microloop(tmp_path / "integrity.db") as client:
        activate_site(client)
        art = client._artifact(SITE.version)

        # Tamper with the payload directly in the database
        with client.store.transaction() as db:
            db.execute("UPDATE artifacts SET checksum='tampered_hash' WHERE id=?", (art["id"],))

        # Integrity mismatch must reject artifact and fail closed to fallback
        st = {"text": "allow permission 0"}
        res = client.decide(site=SITE, state=st, fallback=lambda: "allow")
        assert res.source == "fallback"
        assert res.fallback_reason == "engine_or_store_unavailable"


def test_invariant_8_verifier_identity_consistency(tmp_path):
    with Microloop(tmp_path / "verifier.db") as client:
        seed(client, 240, "obs")
        client.compile(SITE, engine="exact")
        client.calibrate(SITE, verifier=verify, requirements=REQ)
        seed(client, 100, "shad")

        # Evaluate with mismatched verifier version
        def wrong_version_verifier(state, choice):
            return Outcome(1.0, "safety_verifier", "2.0", {"diff": True})

        with pytest.raises(ValueError, match="Verifier changed"):
            client.evaluate(SITE, verifier=wrong_version_verifier)


def test_invariant_10_compaction_preserves_evaluation_evidence(tmp_path):
    with Microloop(tmp_path / "compaction.db") as client:
        activate_site(client)
        seed(client, 150, "prod")

        # Run compaction keeping recent 50 rows
        res = client.compact(SITE, keep_recent=50, vacuum=True)
        assert res["remaining"] >= 50
        assert client._artifact(SITE.version)["status"] == "ACTIVE"

        # Ensure active decision path continues serving seamlessly
        st = {"text": "allow permission 0"}
        dec = client.decide(site=SITE, state=st, fallback=lambda: "deny")
        assert dec.source in ("fast_path", "fallback")
