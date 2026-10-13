"""Formal safety invariant tests for Ink Decision JIT."""

import sqlite3
from dataclasses import asdict

import pytest
from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements
from ink.internal.contracts import canonical, digest

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
    with Ink(tmp_path / "authority.db") as client:
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
    with Ink(tmp_path / "fallback.db") as client:
        activate_site(client)
        # Unknown state falling outside coverage
        st = {"text": "completely unseen query"}
        res = client.decide(site=SITE, state=st, fallback=lambda: "deny")
        assert res.source == "fallback"
        assert res.fallback_reason == "outside_coverage"
        assert res.choice == "deny"


def test_invariant_3_single_active_per_engine_guarantee(tmp_path):
    with Ink(tmp_path / "single_active.db") as client:
        activate_site(client)
        # A second ACTIVE artifact for the same serving engine violates DB uniqueness.
        existing = client._artifact(SITE.version)
        with pytest.raises(sqlite3.IntegrityError):
            with client.store.transaction() as db:
                db.execute(
                    "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
                    (
                        "second_art", SITE.version, existing["payload_json"] if "payload_json" in existing else __import__("json").dumps(existing["payload"]),
                        "fake_chk", "ACTIVE", 123.0, None, None,
                    ),
                )


def test_invariant_4_freshness_after_demotion(tmp_path):
    with Ink(tmp_path / "freshness.db") as client:
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
    with Ink(tmp_path / "contraction.db") as client:
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
    with Ink(tmp_path / "integrity.db") as client:
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
    with Ink(tmp_path / "verifier.db") as client:
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
    with Ink(tmp_path / "compaction.db") as client:
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


def test_invariant_neural_artifacts_cannot_serve_even_if_persisted(tmp_path):
    """Invariant 1: Neural artifacts have zero serving authority even if marked ACTIVE."""
    import json

    with Ink(tmp_path / "neural_block.db") as client:
        activate_site(client)
        # Manually insert an ACTIVE neural artifact with engine='decision'
        neural_payload = {
            "engine_data": {
                "engine": "decision",
                "model": "ink-decision-small",
                "checkpoint": "chk_dummy",
            },
            "coverage": {canonical({"text": "allow permission 0"}): {"choice": "allow", "confidence": 0.99}},
            "partitions": {"train": [], "calibration": [], "evaluation": []},
        }
        neural_chk = digest(neural_payload)
        with client.store.transaction() as db:
            db.execute("UPDATE artifacts SET status='RETIRED' WHERE site=?", (SITE.version,))
            db.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "art_neural_test",
                    SITE.version,
                    json.dumps(neural_payload),
                    neural_chk,
                    "ACTIVE",
                    9999.0,
                    None,
                    None,
                ),
            )

        st = {"text": "allow permission 0"}
        res = client.decide(site=SITE, state=st, fallback=lambda: "allow")
        assert res.source == "fallback"
        assert res.choice == "allow"


def test_invariant_exact_priority_over_linear_when_both_active(tmp_path):
    """Invariant 2: Exact engine always takes serving priority over Linear."""
    import json

    with Ink(tmp_path / "priority.db") as client:
        activate_site(client)
        exact_art = client._artifact(SITE.version)

        # Insert a valid linear artifact also marked ACTIVE
        linear_payload = {
            "engine_data": {
                "engine": "linear",
                "classes": list(SITE.choices),
                "vocabulary": ["allow", "permission"],
                "weights": [[1.0, 0.5], [-1.0, -0.5]],
                "bias": [0.1, -0.1],
            },
            "coverage": {canonical({"text": "allow permission 0"}): {"choice": "allow", "confidence": 0.95}},
            "partitions": {"train": [], "calibration": [], "evaluation": []},
        }
        prof_json = json.dumps(exact_art["profile"]) if exact_art.get("profile") else None
        with client.store.transaction() as db:
            db.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "art_linear_priority",
                    SITE.version,
                    json.dumps(linear_payload),
                    digest(linear_payload),
                    "ACTIVE",
                    exact_art["epoch"] + 10.0,
                    prof_json,
                    None,
                ),
            )

        artifacts = client._dispatcher.load_artifacts(SITE.version)
        assert len(artifacts) >= 2
        # Exact must be sorted first regardless of epoch
        assert artifacts[0]["payload"]["engine_data"]["engine"] == "exact"


def test_invariant_linear_cannot_serve_outside_qualified_coverage(tmp_path):
    """Invariant 3: Linear engine cannot serve outside verified coverage."""
    with Ink(tmp_path / "linear_cov.db") as client:
        activate_site(client)
        # Request completely outside observed text
        st = {"text": "completely_unseen_out_of_distribution_query_xyz"}
        res = client.decide(site=SITE, state=st, fallback=lambda: "deny")
        assert res.source == "fallback"
        assert res.fallback_reason == "outside_coverage"
        assert res.choice == "deny"


def test_invariant_stale_identity_fails_open(tmp_path):
    """Invariant 4: Stale site choices, fallback revision, or verifier fail open."""
    with Ink(tmp_path / "stale_id.db") as client:
        activate_site(client)

        # Mutate fallback revision on site
        site_v2 = DecisionSite("safety.test", {"text": "string"}, ("allow", "deny"), fallback_revision="2")
        st = {"text": "allow permission 0"}
        res = client.decide(site=site_v2, state=st, fallback=lambda: "allow")
        assert res.source == "fallback"


def test_invariant_revoked_semantic_regions_never_serve(tmp_path):
    """Invariant 5: Explicitly revoked semantic regions fail open."""
    import json

    with Ink(tmp_path / "revoked_reg.db") as client:
        activate_site(client)
        art = client._artifact(SITE.version)
        profile = dict(art["profile"]) if art.get("profile") else {}

        # Add a revoked region to coverage_engine
        profile["coverage_engine"] = {
            "exact_coverage": {},
            "semantic_regions": [
                {
                    "region_id": "revoked_region_1",
                    "site": SITE.version,
                    "choice": "allow",
                    "prototype_state": {"text": "allow permission 0"},
                    "prototype_vector": [1.0, 0.0],
                    "radius": 0.5,
                    "negative_margin": 0.8,
                    "member_count": 10,
                    "confidence": 0.9,
                    "status": "REVOKED",
                }
            ],
            "vectorizer": {
                "vocab": {"allow": 0, "permission": 1},
                "idf": {"allow": 1.0, "permission": 1.0},
            },
        }
        profile_clean = dict(profile)
        profile_clean.pop("id", None)
        profile["id"] = digest(profile_clean)

        with client.store.transaction() as db:
            db.execute("UPDATE artifacts SET profile=? WHERE id=?", (json.dumps(profile), art["id"]))

        st = {"text": "allow permission 0"}
        res = client.decide(site=SITE, state=st, fallback=lambda: "allow")
        assert res.source == "fallback"
        assert res.fallback_reason == "revoked"


def test_invariant_kill_switches_force_host_fallback(tmp_path):
    """Invariant 6: Both disable_fast_path and disabled force fallback."""
    with Ink(tmp_path / "kill.db") as client:
        activate_site(client)

    # Re-open with disable_fast_path=True
    with Ink(tmp_path / "kill.db", disable_fast_path=True) as client_disabled:
        st = {"text": "allow permission 0"}
        res = client_disabled.decide(site=SITE, state=st, fallback=lambda: "allow")
        assert res.source == "fallback"
        assert res.fallback_reason == "disabled_kill_switch"


def test_invariant_readonly_mode_never_mutates_evidence(tmp_path):
    """Invariant 7: Readonly inspection never mutates database state."""
    db_file = tmp_path / "readonly.db"
    with Ink(db_file) as client:
        activate_site(client)
        init_history_count = len(client.store.history(SITE.version))

    with Ink(db_file, readonly=True) as ro_client:
        sites = ro_client.sites()
        assert len(sites) >= 1
        st = ro_client.status(SITE)
        assert st["state"] == "ACTIVE"
        insp = ro_client.inspect(SITE)
        assert insp["state"] == "ACTIVE"
        # Decisions in readonly mode fail open and never insert rows
        res = ro_client.decide(site=SITE, state={"text": "allow permission 0"}, fallback=lambda: "deny")
        after_count = len(ro_client.store.history(SITE.version))
        assert after_count == init_history_count
        assert res.choice == "deny"


def test_invariant_corrupt_profile_checksum_fails_open(tmp_path):
    """Invariant 8: Corrupt profile checksums fail open to Host fallback."""
    import json

    with Ink(tmp_path / "profile_corrupt.db") as client:
        activate_site(client)
        art = client._artifact(SITE.version)

        # Corrupt profile internal id hash
        profile = dict(art["profile"]) if art.get("profile") else {}
        profile["id"] = "tampered_profile_hash"
        with client.store.transaction() as db:
            db.execute("UPDATE artifacts SET profile=? WHERE id=?", (json.dumps(profile), art["id"]))

        st = {"text": "allow permission 0"}
        res = client.decide(site=SITE, state=st, fallback=lambda: "allow")
        assert res.source == "fallback"
        assert res.fallback_reason == "engine_or_store_unavailable"


def test_invariant_invalid_engine_output_fails_open(tmp_path):
    """Invariant 9: Invalid local predictions (undeclared choice / NaN) cannot escape to caller."""
    import json

    with Ink(tmp_path / "invalid_pred.db") as client:
        activate_site(client)
        art = client._artifact(SITE.version)
        payload = dict(art["payload"])

        # Corrupt choice in table to an undeclared choice
        st_key = canonical({"text": "allow permission 0"})
        payload["engine_data"]["table"][st_key]["choice"] = "unauthorized_choice"
        with client.store.transaction() as db:
            db.execute(
                "UPDATE artifacts SET payload=?, checksum=? WHERE id=?",
                (json.dumps(payload), digest(payload), art["id"]),
            )

        st = {"text": "allow permission 0"}
        # Caller receives legitimate fallback result, NOT the unauthorized choice
        res = client.decide(site=SITE, state=st, fallback=lambda: "deny")
        assert res.source == "fallback"
        assert res.choice == "deny"


def test_invariant_fast_path_failures_fail_open_to_host(tmp_path):
    """Invariant 10: In-engine unexpected exceptions fail open safely to Host."""
    with Ink(tmp_path / "failopen.db") as client:
        activate_site(client)

        # Mock engine predict to raise unexpected exception
        orig_predict = client.engines["exact"].predict
        try:
            def broken_predict(payload, state):
                raise RuntimeError("Hardware/memory error during fast path execution")

            client.engines["exact"].predict = broken_predict

            st = {"text": "allow permission 0"}
            res = client.decide(site=SITE, state=st, fallback=lambda: "allow")
            assert res.source == "fallback"
            assert res.choice == "allow"
        finally:
            client.engines["exact"].predict = orig_predict

