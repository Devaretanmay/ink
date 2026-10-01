"""Chaos and crash recovery tests for Microloop Decision JIT."""

import concurrent.futures
from dataclasses import asdict

import pytest
from microloop import DecisionSite, FallbackResult, Microloop, Outcome, PromotionRequirements
from microloop.internal.contracts import canonical
from microloop.internal.coverage import CoverageEngine, SemanticRegion

SITE = DecisionSite("order.action", {"text": "string"}, ("cancel", "fulfill"))
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
    exp = "cancel" if "cancel" in state["text"] else "fulfill"
    return Outcome(float(choice == exp), "test_verifier", "1", {"expected": exp})


def seed(client, count, prefix="seed"):
    for i in range(count):
        if i % 2 == 0:
            text = f"request cancel order {i % 4}"
            exp = "cancel"
        else:
            text = f"fulfill package shipment {i % 4}"
            exp = "fulfill"
        st = {"text": text}
        res = client.decide(
            site=SITE,
            state=st,
            task_id=f"{prefix}-{i}",
            fallback=lambda exp=exp: FallbackResult(exp, model_calls=1),
        )
        client.record_outcome(res.decision_id, **asdict(verify(st, res.choice)))


def setup_active_site(client):
    seed(client, 300, prefix="setup_obs")
    client.compile(SITE, engine="exact")
    client.calibrate(SITE, verifier=verify, requirements=REQ)
    seed(client, 100, prefix="setup_shadow")
    eval_res = client.evaluate(SITE, verifier=verify)
    assert eval_res["qualified"]


def test_corrupted_region_parameters_abstain_safely(tmp_path):
    with Microloop(tmp_path / "corrupt_region.db") as client:
        setup_active_site(client)
        art = client._artifact(SITE.version)
        prof = art["profile"]
        cov_engine = CoverageEngine.from_dict(prof["coverage_engine"])

        # Inject corrupted region parameters: non-positive radius and negative margin
        bad_region = SemanticRegion(
            region_id="corrupt-001",
            site=SITE.version,
            choice="cancel",
            prototype_state={"text": "corrupt"},
            prototype_vector=[0.0] * len(cov_engine.semantic_regions[0].prototype_vector),
            radius=-1.0,
            negative_margin=-0.5,
            member_count=1,
            confidence=0.9,
            status="ACTIVE",
        )
        cov_engine.semantic_regions.append(bad_region)
        prof["coverage_engine"] = cov_engine.to_dict()

        with client.store.transaction() as db:
            db.execute("UPDATE artifacts SET profile=? WHERE id=?", (canonical(prof), art["id"]))

        # Query should not crash; must safely fall back
        st = {"text": "request cancel order 0"}
        res = client.decide(site=SITE, state=st, fallback=lambda: "cancel")
        assert res.choice == "cancel"
        assert res.source in ("fast_path", "fallback")


def test_raising_verifier_leaves_state_unharmed(tmp_path):
    with Microloop(tmp_path / "raising_verifier.db") as client:
        seed(client, 300, prefix="pre_compile")
        client.compile(SITE, engine="exact")
        client.calibrate(SITE, verifier=verify, requirements=REQ)
        seed(client, 100, prefix="pre_eval")

        def exploding_verifier(state, choice):
            raise RuntimeError("Database connection timed out during verification")

        with pytest.raises(RuntimeError, match="Database connection timed out"):
            client.evaluate(SITE, verifier=exploding_verifier)

        # Candidate must remain in SHADOW, not promoted to ACTIVE
        art = client._artifact(SITE.version)
        assert art["status"] == "SHADOW"
        assert client.health(SITE)["status"] == "SHADOW"

        # Active artifact count is strictly 0
        active_count = len(
            client.store.rows(
                "SELECT id FROM artifacts WHERE site=? AND status='ACTIVE'",
                (SITE.version,),
            )
        )
        assert active_count == 0


def test_hot_swap_concurrent_load_and_atomic_transition(tmp_path):
    with Microloop(tmp_path / "concurrent_swap.db") as client:
        setup_active_site(client)
        art_old = client._artifact(SITE.version)
        old_id = art_old["id"]

        # Prepare new qualified artifact
        new_id = client.compile(SITE, engine="exact", replace_existing=True)
        client.calibrate(SITE, verifier=verify, requirements=REQ)
        seed(client, 100, prefix="swap_shadow")
        eval_res = client.evaluate(SITE, verifier=verify, auto_promote=False)
        assert eval_res["qualified"]

        serving_errors = []
        observed_artifacts = set()

        def worker(idx):
            try:
                st = {"text": f"request cancel order {idx % 4}"}
                res = client.decide(
                    site=SITE,
                    state=st,
                    task_id=f"worker-{idx}",
                    fallback=lambda: "cancel",
                )
                if res.fast_path_version:
                    observed_artifacts.add(res.fast_path_version)
                return True
            except Exception as e:
                serving_errors.append(f"{type(e).__name__}: {e}")
                return False

        # Execute 150 concurrent decide calls while executing hot-swap
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(worker, i) for i in range(75)]
            swapped = client.hot_swap(SITE, new_id)
            assert swapped == new_id
            futures.extend([pool.submit(worker, i) for i in range(75, 150)])
            for f in concurrent.futures.as_completed(futures):
                f.result()

        assert serving_errors == []
        active_arts = client.store.rows(
            "SELECT id FROM artifacts WHERE site=? AND status='ACTIVE'",
            (SITE.version,),
        )
        assert len(active_arts) == 1
        assert active_arts[0]["id"] == new_id

        # Verify old artifact is retired
        old_row = client.store.rows("SELECT status FROM artifacts WHERE id=?", (old_id,))[0]
        assert old_row["status"] == "RETIRED"


def test_single_active_invariant_under_restart(tmp_path):
    db_path = tmp_path / "restart.db"
    with Microloop(db_path) as client:
        setup_active_site(client)
        art = client._artifact(SITE.version)
        assert art["status"] == "ACTIVE"

    # Reopen database and verify single active artifact invariant
    with Microloop(db_path) as client2:
        active_rows = client2.store.rows(
            "SELECT id FROM artifacts WHERE site=? AND status='ACTIVE'",
            (SITE.version,),
        )
        assert len(active_rows) == 1
        res = client2.decide(
            site=SITE,
            state={"text": "request cancel order 0"},
            fallback=lambda: "cancel",
        )
        assert res.source in ("fast_path", "fallback")
