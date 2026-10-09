"""Phase 10: Progressive Online Authority & Production Hardening Unit Tests."""

import json
from pathlib import Path

import pytest
from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements
from ink.internal.coverage import CoverageEngine, SemanticRegion, TextVectorizer


def test_phase10_ambiguity_margin_disambiguation():
    """Adversarial cross-intent boundary queries must return 'ambiguous' and fall back."""
    vocab = {"dispute": 0, "card": 1, "fee": 2, "transfer": 3, "payment": 4}
    idf = {w: 1.0 for w in vocab}
    vectorizer = TextVectorizer(vocab=vocab, idf=idf)

    # Prototype A: "card dispute" (choice: card_support)
    # Prototype B: "transfer payment" (choice: transfer_support)
    vec_a = vectorizer.transform("card dispute").tolist()
    vec_b = vectorizer.transform("transfer payment").tolist()

    reg_a = SemanticRegion(
        region_id="reg_card",
        site="site_v1",
        choice="card_support",
        prototype_state={"request": "card dispute"},
        prototype_vector=vec_a,
        radius=0.55,
        negative_margin=0.90,
        member_count=50,
        confidence=0.95,
        status="ACTIVE",
    )
    reg_b = SemanticRegion(
        region_id="reg_transfer",
        site="site_v1",
        choice="transfer_support",
        prototype_state={"request": "transfer payment"},
        prototype_vector=vec_b,
        radius=0.55,
        negative_margin=0.90,
        member_count=50,
        confidence=0.95,
        status="ACTIVE",
    )

    engine = CoverageEngine(
        exact_coverage={},
        semantic_regions=[reg_a, reg_b],
        vectorizer=vectorizer,
        ambiguity_margin=0.15,
    )

    # 1. Clear un-ambiguous card query
    level, reg, conf = engine.route({"request": "card dispute"})
    assert level == "semantic"
    assert reg["choice"] == "card_support"

    # 2. Clear un-ambiguous transfer query
    level, reg, conf = engine.route({"request": "transfer payment"})
    assert level == "semantic"
    assert reg["choice"] == "transfer_support"

    # 3. Adversarial crossover query mixing both intents equally
    level, reg, conf = engine.route({"request": "card dispute transfer payment"})
    assert level == "ambiguous"
    assert reg is None

    # 4. State close to A boundary but with competing B hypothesis within margin
    level, reg, conf = engine.route({"request": "card transfer"})
    assert level == "ambiguous"
    assert reg is None

    # 5. Completely outside coverage
    level, reg, conf = engine.route({"request": "order pizza tonight"})
    assert level == "outside_coverage"


def test_phase10_decide_ambiguity_fails_open_to_fallback(tmp_path):
    """Ink.decide() must safely fall back and never serve locally when boundary is ambiguous."""
    db_path = str(tmp_path / "ambig.db")
    site = DecisionSite("test.routing", {"text": "string"}, ("card_support", "transfer_support"))
    reqs = PromotionRequirements(6, 0.5, 0.1, 1.0, 0.05, 2, 50)

    def verifier(state, choice):
        return Outcome(1.0, "rule", "1")

    with Ink(db_path) as ink:
        # Observe repeated card samples and transfer samples (60 samples -> 6 per state in calibration)
        for i in range(60):
            st_card = {"text": "card balance inquiry"}
            rc = ink.decide(site=site, state=st_card, fallback=lambda: FallbackResult("card_support", model_calls=1), task_id=f"obc_{i}")
            ink.record_outcome(rc.decision_id, **verifier(st_card, rc.choice).__dict__)

            st_trans = {"text": "wire transfer payment"}
            rt = ink.decide(site=site, state=st_trans, fallback=lambda: FallbackResult("transfer_support", model_calls=1), task_id=f"obt_{i}")
            ink.record_outcome(rt.decision_id, **verifier(st_trans, rt.choice).__dict__)

        # Compile and calibrate
        ink.compile(site, engine="exact")
        ink.calibrate(site, verifier=verifier, requirements=reqs)

        # Shadow traffic
        for i in range(12):
            st_card = {"text": "card balance inquiry"}
            rc = ink.decide(site=site, state=st_card, fallback=lambda: FallbackResult("card_support", model_calls=1), task_id=f"shc_{i}")
            ink.record_outcome(rc.decision_id, **verifier(st_card, rc.choice).__dict__)

            st_trans = {"text": "wire transfer payment"}
            rt = ink.decide(site=site, state=st_trans, fallback=lambda: FallbackResult("transfer_support", model_calls=1), task_id=f"sht_{i}")
            ink.record_outcome(rt.decision_id, **verifier(st_trans, rt.choice).__dict__)

        ink.evaluate(site, verifier=verifier, auto_promote=True)
        assert ink.inspect(site)["state"] == "ACTIVE"

        # Query with crossover text containing features of both intents
        crossover_state = {"text": "card balance wire transfer payment"}
        res = ink.decide(
            site=site,
            state=crossover_state,
            fallback=lambda: FallbackResult("card_support", model_calls=1),
        )
        assert res.source == "fallback"
        assert res.fallback_reason in ("ambiguous", "outside_coverage")
        assert res.receipt["served_by"] == "fallback"


def test_phase10_progressive_online_evidence_and_persistence(tmp_path):
    """Progressive online evidence accumulates across batches and persists in SQLite."""
    db_path = str(tmp_path / "prog.db")
    site = DecisionSite("prog.site", {"text": "string"}, ("approve", "reject"))
    reqs = PromotionRequirements(6, 0.5, 0.1, 1.0, 0.05, 2, 50)

    def verifier(state, choice):
        exp = "approve" if "good" in state["text"] else "reject"
        return Outcome(1.0 if choice == exp else 0.0, "verifier_v1", "1", {"expected": exp})

    with Ink(db_path, app_id="test_app") as ink:
        # 1. Observe initial training data (60 samples -> 6 per state in calibration)
        for i in range(60):
            st = {"text": "good user sample" if i % 2 == 0 else "bad user sample"}
            ch = "approve" if i % 2 == 0 else "reject"
            res = ink.decide(site=site, state=st, fallback=lambda c=ch: FallbackResult(c, model_calls=1), task_id=f"ob_{i}")
            ink.record_outcome(res.decision_id, **verifier(st, res.choice).__dict__)

        # 2. Compile and calibrate
        art_id = ink.compile(site, engine="exact")
        ink.calibrate(site, verifier=verifier, requirements=reqs)

        # Verify evidence_epoch was created and opened
        epochs = ink.store.rows("SELECT * FROM evidence_epochs WHERE app_id='test_app'")
        assert len(epochs) == 1
        assert epochs[0]["status"] == "OPEN"
        assert epochs[0]["artifact_id"] == art_id

        # 3. Simulate progressive shadow decisions and outcomes
        for i in range(12):
            st = {"text": "good user sample" if i % 2 == 0 else "bad user sample"}
            ch = "approve" if i % 2 == 0 else "reject"
            res = ink.decide(site=site, state=st, fallback=lambda c=ch: FallbackResult(c, model_calls=1), task_id=f"sh_{i}")
            ink.record_outcome(res.decision_id, **verifier(st, res.choice).__dict__)

        # Verify progressive evidence was recorded in SQLite
        prog_ev = ink.store.get_progressive_evidence(epochs[0]["epoch_id"])
        assert len(prog_ev) > 0
        for ev in prog_ev:
            assert ev["quality"] == 1.0
            assert ev["verified_match"] == 1


def test_phase10_process_restart_multi_run_roundtrip(tmp_path):
    """Evidence accumulation, qualification, and serving survive clean process restarts."""
    db_path = str(tmp_path / "restart.db")
    site = DecisionSite("restart.site", {"query": "string"}, ("allow", "deny"))
    reqs = PromotionRequirements(6, 0.5, 0.1, 1.0, 0.05, 2, 50)

    def verifier(state, choice):
        exp = "allow" if "allow" in state["query"] else "deny"
        return Outcome(1.0 if choice == exp else 0.0, "v_rule", "1", {"expected": exp})

    # --- PROCESS RUN 1: Observe, compile, calibrate, shutdown ---
    with Ink(db_path) as ink1:
        for i in range(60):
            q = "allow user" if i % 2 == 0 else "deny user"
            ch = "allow" if i % 2 == 0 else "deny"
            r = ink1.decide(site=site, state={"query": q}, fallback=lambda c=ch: FallbackResult(c, model_calls=1), task_id=f"p1_{i}")
            ink1.record_outcome(r.decision_id, **verifier({"query": q}, r.choice).__dict__)
        art_id = ink1.compile(site, engine="exact")
        ink1.calibrate(site, verifier=verifier, requirements=reqs)

    # --- PROCESS RUN 2: Reopen, accumulate shadow batch 1, shutdown ---
    with Ink(db_path) as ink2:
        for i in range(6):
            q = "allow user" if i % 2 == 0 else "deny user"
            ch = "allow" if i % 2 == 0 else "deny"
            r = ink2.decide(site=site, state={"query": q}, fallback=lambda c=ch: FallbackResult(c, model_calls=1), task_id=f"p2_{i}")
            ink2.record_outcome(r.decision_id, **verifier({"query": q}, r.choice).__dict__)

    # --- PROCESS RUN 3: Reopen, accumulate shadow batch 2, qualify & promote, shutdown ---
    with Ink(db_path) as ink3:
        for i in range(6):
            q = "allow user" if i % 2 == 0 else "deny user"
            ch = "allow" if i % 2 == 0 else "deny"
            r = ink3.decide(site=site, state={"query": q}, fallback=lambda c=ch: FallbackResult(c, model_calls=1), task_id=f"p3_{i}")
            ink3.record_outcome(r.decision_id, **verifier({"query": q}, r.choice).__dict__)

        # Evaluate cumulative shadow evidence across both runs!
        res_eval = ink3.evaluate(site, verifier=verifier, auto_promote=True)
        assert res_eval["qualified"] is True
        insp = ink3.inspect(site)
        assert insp["state"] == "ACTIVE"

    # --- PROCESS RUN 4: Reopen, serve via Fast Path! ---
    with Ink(db_path) as ink4:
        fast_path_served = False
        for _ in range(5):
            r = ink4.decide(
                site=site,
                state={"query": "allow user"},
                fallback=lambda: FallbackResult("deny", model_calls=1),
            )
            if r.source == "fast_path":
                assert r.choice == "allow"
                fast_path_served = True
                break
            assert r.fallback_reason == "comparison"
        assert fast_path_served is True


def test_phase10_application_namespace_isolation(tmp_path):
    """Two apps with identical state schema must isolate evidence epochs completely."""
    db_path = str(tmp_path / "isolation.db")
    site = DecisionSite("shared.site", {"text": "string"}, ("cat_a", "cat_b"))
    reqs = PromotionRequirements(6, 0.5, 0.1, 1.0, 0.05, 2, 50)

    def verifier(state, choice):
        return Outcome(1.0, "ver", "1")

    with Ink(db_path, app_id="app_alpha") as client_alpha:
        # Observe 60 samples, compile, calibrate under App Alpha
        for i in range(60):
            st = {"text": "item_a" if i % 2 == 0 else "item_b"}
            ch = "cat_a" if i % 2 == 0 else "cat_b"
            r = client_alpha.decide(site=site, state=st, fallback=lambda c=ch: FallbackResult(c, model_calls=1), task_id=f"al_{i}")
            client_alpha.record_outcome(r.decision_id, **verifier(st, ch).__dict__)
        client_alpha.compile(site, engine="exact")
        client_alpha.calibrate(site, verifier=verifier, requirements=reqs)

    # Check that App Alpha owns its open epoch
    with Ink(db_path, app_id="app_alpha") as client_alpha:
        alpha_epochs = client_alpha.store.rows("SELECT * FROM evidence_epochs WHERE app_id='app_alpha'")
        assert len(alpha_epochs) == 1
        assert alpha_epochs[0]["status"] == "OPEN"

    # Verify App Beta sees zero epochs for itself
    with Ink(db_path, app_id="app_beta") as client_beta:
        beta_epochs = client_beta.store.rows("SELECT * FROM evidence_epochs WHERE app_id='app_beta'")
        assert len(beta_epochs) == 0


def test_phase10_drift_revocation_and_epochs(tmp_path):
    """Sudden drift demotes active artifact and revokes evidence epoch."""
    db_path = str(tmp_path / "drift.db")
    site = DecisionSite("drift.site", {"tier": "string"}, ("pass", "fail"))
    reqs = PromotionRequirements(6, 0.5, 0.1, 1.0, 0.35, 2, 50)

    def verifier(state, choice):
        return Outcome(1.0 if choice == "pass" else 0.0, "v", "1")

    with Ink(db_path, app_id="app_drift") as ink:
        # Train & promote
        for i in range(60):
            r = ink.decide(site=site, state={"tier": "gold"}, fallback=lambda: FallbackResult("pass", model_calls=1), task_id=f"tr_{i}")
            ink.record_outcome(r.decision_id, **verifier({"tier": "gold"}, "pass").__dict__)
        art_id = ink.compile(site, engine="exact")
        ink.calibrate(site, verifier=verifier, requirements=reqs)
        for i in range(12):
            r = ink.decide(site=site, state={"tier": "gold"}, fallback=lambda: FallbackResult("pass", model_calls=1), task_id=f"sh_{i}")
            ink.record_outcome(r.decision_id, **verifier({"tier": "gold"}, "pass").__dict__)
        ink.evaluate(site, verifier=verifier, auto_promote=True)
        assert ink.inspect(site)["state"] == "ACTIVE"

        # Serve active (first with good outcomes)
        for i in range(10):
            r = ink.decide(site=site, state={"tier": "gold"}, fallback=lambda: FallbackResult("pass", model_calls=1), task_id=f"act_{i}")
            ink.record_outcome(r.decision_id, quality=1.0, verifier="v", verifier_version="1")

        # Inject severe drift on active served traffic (60 bad outcomes on served path)
        for i in range(60):
            r = ink.decide(site=site, state={"tier": "gold"}, fallback=lambda: FallbackResult("pass", model_calls=1), task_id=f"bad_{i}")
            ink.record_outcome(r.decision_id, quality=0.0, verifier="v", verifier_version="1")

        reeval = ink.reevaluate(site)
        assert reeval["demoted"] is True
        assert ink.inspect(site)["state"] == "SHADOW"

        # Verify epoch was marked REVOKED
        rev_epochs = ink.store.rows("SELECT * FROM evidence_epochs WHERE artifact_id=? AND status='REVOKED'", (art_id,))
        assert len(rev_epochs) == 1


def test_phase10_cli_doctor(tmp_path, capsys):
    """'ink doctor' command inspects SQLite database health and prints diagnostic."""
    from ink.cli import main as cli_main

    from ink.internal.decision_store import SCHEMA_VERSION

    db_path = str(tmp_path / "doctor.db")
    site = DecisionSite("doc.site", {"k": "string"}, ("a", "b"))
    with Ink(db_path) as ink:
        ink.decide(site=site, state={"k": "v"}, fallback=lambda: FallbackResult("a", model_calls=1))

    # Test text output
    rc = cli_main(["doctor", "--db", db_path])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "INK DOCTOR" in captured
    assert "HEALTHY" in captured

    # Test json output
    rc_json = cli_main(["doctor", "--db", db_path, "--json"])
    assert rc_json == 0
    captured_json = capsys.readouterr().out
    data = json.loads(captured_json)
    assert data["status"] == "healthy"
    assert data["integrity"] == "ok"
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["sites_count"] == 1


def test_phase10_cli_discover_horizon(tmp_path, capsys):
    """'ink discover' outputs authority horizon estimation based on trace volume."""
    from ink.cli import main as cli_main

    trace_file = tmp_path / "traces.jsonl"
    traces = [
        {
            "site_name": "support_routing",
            "state": {"action": "route"},
            "choice": "support",
            "elapsed_ms": 150.0,
            "outcome": 1,
        }
        for _ in range(50)
    ]
    with trace_file.open("w") as f:
        for t in traces:
            f.write(json.dumps(t) + "\n")

    rc = cli_main(["discover", str(trace_file)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "AUTHORITY HORIZON ESTIMATE" in out
    assert "horizon @ 100% acc" in out
    assert "horizon @ 95% acc" in out
