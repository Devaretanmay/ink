"""Production-readiness audit test suite.

Validates:
1. Canary allocation fairness under extreme state skew (90% identical state).
2. Stable experiment unit consistency across multi-step workflow task IDs.
3. Localhost security: DNS rebinding protection (Host header), cross-origin isolation (Origin header),
   and body size limits.
4. Serving control safety: writes disabled by default (403 Forbidden).
5. Counterfactual trace replay immutability (strictly read-only, zero database mutation).
6. Fail-open resilience under storage corruption.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from dataclasses import asdict

import pytest

from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements
from ink.ui.server import start_server


def test_canary_allocation_fairness_under_state_skew(tmp_path):
    """Confirm canary rate distributes uniformly across tasks even when 90% share identical state."""
    site = DecisionSite("skew.canary.site", {"category": "string"}, ("allow", "deny"))
    req = PromotionRequirements(10, 0.5, 0.5, 0.6, 0.25, 5, 100)

    with Ink(tmp_path / "canary_skew.db") as client:
        client.register(site)

        def verify_fn(s, c):
            exp = "allow" if s.get("category") in ("common", "allow") else "deny"
            return Outcome(float(c == exp), "verifier", "1", {"exp": exp})

        # Train and qualify site to ACTIVE
        for i in range(250):
            st = {"category": "common" if i % 2 == 0 else "rare"}
            exp = "allow" if st["category"] == "common" else "deny"
            res = client.decide(
                site=site,
                state=st,
                task_id=f"init-{i}",
                fallback=lambda e=exp: FallbackResult(e, model_calls=1),
            )
            client.record_outcome(res.decision_id, **asdict(verify_fn(st, res.choice)))

        client.maintenance(verifier=verify_fn, requirements=req, engine="exact")
        for i in range(100):
            st = {"category": "common" if i % 2 == 0 else "rare"}
            exp = "allow" if st["category"] == "common" else "deny"
            res = client.decide(
                site=site,
                state=st,
                task_id=f"shadow-{i}",
                fallback=lambda e=exp: FallbackResult(e, model_calls=1),
            )
            client.record_outcome(res.decision_id, **asdict(verify_fn(st, res.choice)))
        client.maintenance(verifier=verify_fn, requirements=req, engine="exact")

        art = client._artifact(site.version)
        assert art["status"] == "ACTIVE"

        # Set 20% canary rate
        client.set_serving_policy(site, mode="CANARY", canary_rate=0.20)

        # Send 500 decisions where 90% have the EXACT SAME state: {"category": "common"}
        fast_count = 0
        holdout_count = 0
        total_evals = 500

        for i in range(total_evals):
            is_common = i < 450  # 90% identical state
            st = {"category": "common" if is_common else "rare"}
            res = client.decide(
                site=site,
                state=st,
                task_id=f"workflow-task-{i}",
                fallback=lambda: "allow",
            )
            if res.source == "fast_path" or res.fallback_reason == "comparison":
                fast_count += 1
            elif res.fallback_reason == "canary_holdout":
                holdout_count += 1

        total_tested = fast_count + holdout_count
        assert total_tested == total_evals
        actual_rate = fast_count / total_evals
        # 20% target with binomial variance across 500 samples (expect between 14% and 27%)
        assert 0.14 <= actual_rate <= 0.27, f"Expected ~20% canary rate, observed {actual_rate:.1%}"


def test_canary_stable_experiment_unit(tmp_path):
    """Ensure that decisions belonging to the same task ID always receive the same assignment."""
    site = DecisionSite("stable.unit.site", {"step": "string"}, ("next", "stop"))
    req = PromotionRequirements(10, 0.5, 0.5, 0.6, 0.25, 5, 100)

    with Ink(tmp_path / "stable_unit.db") as client:
        client.register(site)

        def verify_fn(s, c):
            return Outcome(1.0, "v", "1", {})

        for i in range(250):
            res = client.decide(
                site=site,
                state={"step": "step1"},
                task_id=f"train-{i}",
                fallback=lambda: FallbackResult("next", model_calls=1),
            )
            client.record_outcome(res.decision_id, **asdict(verify_fn({"step": "step1"}, "next")))
        client.maintenance(verifier=verify_fn, requirements=req, engine="exact")

        for i in range(100):
            res = client.decide(
                site=site,
                state={"step": "step1"},
                task_id=f"shadow-{i}",
                fallback=lambda: FallbackResult("next", model_calls=1),
            )
            client.record_outcome(res.decision_id, **asdict(verify_fn({"step": "step1"}, "next")))
        client.maintenance(verifier=verify_fn, requirements=req, engine="exact")

        client.set_serving_policy(site, mode="CANARY", canary_rate=0.50)

        # For 50 distinct workflows, execute 3 steps each
        for wf_idx in range(50):
            tid = f"wf-{wf_idx}"
            res1 = client.decide(
                site=site, state={"step": "step1"}, task_id=tid, fallback=lambda: "next"
            )
            res2 = client.decide(
                site=site, state={"step": "step1"}, task_id=tid, fallback=lambda: "next"
            )
            res3 = client.decide(
                site=site, state={"step": "step1"}, task_id=tid, fallback=lambda: "next"
            )
            # All steps in the same task MUST receive identical canary holdout vs serving assignment
            is_holdout1 = res1.fallback_reason == "canary_holdout"
            is_holdout2 = res2.fallback_reason == "canary_holdout"
            is_holdout3 = res3.fallback_reason == "canary_holdout"
            assert is_holdout1 == is_holdout2 == is_holdout3


def test_localhost_http_security_guards(tmp_path):
    """Verify DNS rebinding, cross-origin, and write safety controls."""
    with Ink(tmp_path / "sec_test.db") as client:
        server = start_server(
            client, host="127.0.0.1", port=4244, open_browser=False, background=True
        )
        try:
            base_url = "http://127.0.0.1:4244"

            # 1. DNS Rebinding: request with forged external Host header must be rejected with 403
            req_host = urllib.request.Request(
                f"{base_url}/api/health", headers={"Host": "attacker.com"}
            )
            with pytest.raises(urllib.error.HTTPError) as exc_host:
                urllib.request.urlopen(req_host)
            assert exc_host.value.code == 403

            # 2. Cross-Origin: request with malicious external Origin must be rejected with 403
            req_origin = urllib.request.Request(
                f"{base_url}/api/overview",
                headers={"Host": "127.0.0.1:4244", "Origin": "https://malicious-website.com"},
            )
            with pytest.raises(urllib.error.HTTPError) as exc_origin:
                urllib.request.urlopen(req_origin)
            assert exc_origin.value.code == 403

            # 3. Legitimate request with valid Host header succeeds
            req_valid = urllib.request.Request(
                f"{base_url}/api/health", headers={"Host": "127.0.0.1:4244"}
            )
            with urllib.request.urlopen(req_valid) as resp:
                assert resp.status == 200

            # 4. Serving control writes are disabled over HTTP
            req_post = urllib.request.Request(
                f"{base_url}/api/sites/test/policy",
                data=json.dumps({"mode": "ACTIVE"}).encode(),
                headers={"Host": "127.0.0.1:4244", "Content-Type": "application/json"},
                method="POST",
            )
            with pytest.raises(urllib.error.HTTPError) as exc_post:
                urllib.request.urlopen(req_post)
            assert exc_post.value.code in (403, 405)

            # 5. Body payload limit: oversized body (>1MB) returns 413
            oversized_data = b"x" * (1024 * 1024 + 10)
            req_large = urllib.request.Request(
                f"{base_url}/api/decisions/dummy/replay",
                data=oversized_data,
                headers={"Host": "127.0.0.1:4244", "Content-Type": "application/json"},
                method="POST",
            )
            with pytest.raises(urllib.error.HTTPError) as exc_large:
                urllib.request.urlopen(req_large)
            assert exc_large.value.code == 413
        finally:
            server.shutdown()
            server.server_close()


def test_counterfactual_replay_is_strictly_read_only(tmp_path):
    """Replay simulation must NEVER mutate the database or synthesize fake observations."""
    site = DecisionSite("replay.immutability.site", {"query": "string"}, ("a", "b"))
    with Ink(tmp_path / "replay_immut.db") as client:
        client.register(site)
        res = client.decide(
            site=site,
            state={"query": "test"},
            fallback=lambda: "a",
        )

        server = start_server(
            client, host="127.0.0.1", port=4245, open_browser=False, background=True
        )
        try:
            base_url = "http://127.0.0.1:4245"
            dec_cnt_before = client.store.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[
                0
            ]
            out_cnt_before = client.store.conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[
                0
            ]
            art_cnt_before = client.store.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[
                0
            ]

            req = urllib.request.Request(
                f"{base_url}/api/decisions/{res.decision_id}/replay",
                data=b"{}",
                headers={"Host": "127.0.0.1:4245", "Content-Type": "application/json"},
                method="POST",
            )
            # Replay against unqualified site returns 400 Bad Request (NO_ACTIVE_ARTIFACT)
            with pytest.raises(urllib.error.HTTPError) as exc_replay:
                urllib.request.urlopen(req)
            assert exc_replay.value.code == 400

            # Assert database state remains 100% identical (zero mutations)
            dec_cnt_after = client.store.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[
                0
            ]
            out_cnt_after = client.store.conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
            art_cnt_after = client.store.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[
                0
            ]

            assert dec_cnt_before == dec_cnt_after
            assert out_cnt_before == out_cnt_after
            assert art_cnt_before == art_cnt_after
        finally:
            server.shutdown()
            server.server_close()


def test_unknown_and_unverified_outcomes_prevent_improper_promotion():
    """Verify that a candidate with many unknown outcomes cannot be promoted improperly.

    When 80% of tasks have unknown/unverified outcomes, both the Wilson lower bound
    and Hoeffding lower bound penalize them as non-positive, strictly gating promotion.
    """
    from ink.internal.contracts import Outcome
    from ink.internal.verification import EvidenceStatus, passes, region_qualifies, statistics

    records = []
    # 20 positive verified agreement tasks
    for i in range(20):
        records.append(
            {
                "id": f"rec-{i}",
                "task": f"task-{i}",
                "state": {"input": f"query-{i}"},
                "choice": "allow",
                "agreement": True,
                "status": EvidenceStatus.OBSERVED_AGREEMENT,
                "candidate": {"quality": 1.0, "verifier": "obs", "verifier_version": "1"},
                "baseline": {"quality": 1.0, "verifier": "obs", "verifier_version": "1"},
            }
        )

    # 80 unverified disagreement / unknown tasks
    for i in range(20, 100):
        records.append(
            {
                "id": f"rec-{i}",
                "task": f"task-{i}",
                "state": {"input": f"query-{i}"},
                "choice": "other",
                "agreement": False,
                "status": EvidenceStatus.UNVERIFIED_DISAGREEMENT,
                "candidate": {"quality": 0.0, "verifier": "obs", "verifier_version": "1"},
                "baseline": {"quality": 1.0, "verifier": "obs", "verifier_version": "1"},
            }
        )

    stats = statistics(records)
    assert stats["samples"] == 100
    assert stats["observed_positive"] == 20
    assert stats["unverified_disagreements"] == 80
    assert stats["unknown"] == 80

    # Wilson lower bound for 20 successes out of 100 tasks is ~0.133 (13.3%)
    assert stats["quality_wilson_lower"] < 0.15
    # Hoeffding lower bound is 0.20 - sqrt(ln(20)/200) ~= 0.078
    assert stats["quality_lower"] < 0.10

    req = PromotionRequirements(
        min_samples=30,
        min_quality=0.90,
        min_confidence=0.90,
        max_degradation=0.05,
        comparison_rate=0.25,
        min_region_samples=5,
        evaluation_window=100,
    )
    assert not passes(stats, req), "Candidate with 80% unknown outcomes must fail passes()"
    assert not region_qualifies(stats, req), "Candidate with 80% unknown outcomes must fail region_qualifies()"


def test_http_policy_mutations_strictly_disabled_and_failsafe_exception_handling(tmp_path):
    """v0.7.0 Local Developer Workbench is strictly read-only over HTTP.
    
    1. HTTP policy mutations return 405 Method Not Allowed.
    2. /api/auth/token returns 404 Not Found.
    3. Programmatic policy configuration via Python client API is fully preserved.
    4. get_serving_policy fails safe to HOST_OVERRIDE on database failures and never silently overrides Host safety.
    """
    import sqlite3
    site = DecisionSite("readonly.http.site", {"q": "string"}, ("a", "b"))
    req = PromotionRequirements(10, 0.5, 0.5, 0.6, 0.25, 5, 100)

    db_path = tmp_path / "policy_test.db"
    with Ink(db_path) as client:
        client.register(site)
        for i in range(50):
            res = client.decide(site=site, state={"q": "x"}, task_id=f"t-{i}", fallback=lambda: "a")
            client.record_outcome(res.decision_id, quality=1.0, verifier="v", verifier_version="1")
        client.maintenance(verifier=lambda s, c: Outcome(1.0, "v", "1", {}), requirements=req, engine="exact")

        server = start_server(
            client, host="127.0.0.1", port=4246, open_browser=False, background=True
        )
        try:
            base_url = "http://127.0.0.1:4246"

            # 1. /api/auth/token endpoint must not exist (returns 404)
            req_token = urllib.request.Request(f"{base_url}/api/auth/token", headers={"Host": "127.0.0.1:4246"})
            with pytest.raises(urllib.error.HTTPError) as exc_tok:
                urllib.request.urlopen(req_token)
            assert exc_tok.value.code == 404

            # 2. POST to /api/sites/<site>/policy must return 405 Method Not Allowed
            req_post = urllib.request.Request(
                f"{base_url}/api/sites/{site.name}/policy",
                data=json.dumps({"mode": "HOST_OVERRIDE"}).encode(),
                headers={"Host": "127.0.0.1:4246", "Content-Type": "application/json"},
                method="POST",
            )
            with pytest.raises(urllib.error.HTTPError) as exc_post:
                urllib.request.urlopen(req_post)
            assert exc_post.value.code == 405
            body = json.loads(exc_post.value.read().decode())
            assert body["code"] == "HTTP_MUTATIONS_DISABLED"

            # 3. Programmatic Python client API works as designed
            updated = client.set_serving_policy(site.name, mode="HOST_OVERRIDE", canary_rate=0.0)
            assert updated["mode"] == "HOST_OVERRIDE"
            assert updated["canary_rate"] == 0.0
            pol = client.get_serving_policy(site.name)
            assert pol["mode"] == "HOST_OVERRIDE"
            assert pol["canary_rate"] == 0.0

            # 4. Exception handling in get_serving_policy:
            # A) Simulate missing table (legacy schema): returns ACTIVE default
            class LegacyStoreMock:
                def rows(self, sql, params):
                    raise sqlite3.OperationalError("no such table: site_serving_policies")

            fake_store = client.store.__class__.__new__(client.store.__class__)
            fake_store.rows = LegacyStoreMock().rows
            legacy_pol = client.store.__class__.get_serving_policy(fake_store, site.version)
            assert legacy_pol["mode"] == "ACTIVE"
            assert legacy_pol["canary_rate"] == 1.0

            # B) Simulate database failure/corruption: MUST fail safe to HOST_OVERRIDE (0.0)
            class CorruptStoreMock:
                def rows(self, sql, params):
                    raise sqlite3.DatabaseError("database disk image is malformed")

            fake_store.rows = CorruptStoreMock().rows
            failsafe_pol = client.store.__class__.get_serving_policy(fake_store, site.version)
            assert failsafe_pol["mode"] == "HOST_OVERRIDE"
            assert failsafe_pol["canary_rate"] == 0.0

            # C) Simulate unexpected database operational error: MUST fail safe to HOST_OVERRIDE (0.0)
            class LockedStoreMock:
                def rows(self, sql, params):
                    raise sqlite3.OperationalError("database is locked")

            fake_store.rows = LockedStoreMock().rows
            locked_pol = client.store.__class__.get_serving_policy(fake_store, site.version)
            assert locked_pol["mode"] == "HOST_OVERRIDE"
            assert locked_pol["canary_rate"] == 0.0

        finally:
            server.shutdown()
            server.server_close()


def test_console_xss_escaping():
    """Verify that the console assets properly escape HTML to prevent stored XSS."""
    from ink.ui.assets import INDEX_HTML

    assert "function escapeHTML(str)" in INDEX_HTML
    assert "&amp;" in INDEX_HTML
    assert "&lt;" in INDEX_HTML
    assert "&gt;" in INDEX_HTML
    assert "&quot;" in INDEX_HTML
    assert "&#39;" in INDEX_HTML
    assert "escapeHTML(d.site_name" in INDEX_HTML
    assert "escapeHTML(d.choice)" in INDEX_HTML
    assert "escapeHTML(d.reason" in INDEX_HTML
    assert "escapeHTML(JSON.stringify(d.state, null, 2))" in INDEX_HTML
    assert "encodeURIComponent" in INDEX_HTML


def test_dispatcher_serving_policy_exception_failsafe(tmp_path):
    """When serving policy lookup throws an operational/unexpected exception, fail safe to Host."""
    site = DecisionSite("fail.policy.site", {"k": "string"}, ("a", "b"))
    req = PromotionRequirements(10, 0.5, 0.5, 0.6, 0.25, 5, 100)

    with Ink(tmp_path / "fail_policy.db") as client:
        client.register(site)

        def verify_fn(s, c):
            return Outcome(1.0, "verifier", "1", {})

        # Train and qualify site to ACTIVE
        for i in range(250):
            res = client.decide(
                site=site,
                state={"k": "v"},
                task_id=f"t-{i}",
                fallback=lambda: "a",
            )
            client.record_outcome(res.decision_id, **asdict(verify_fn({"k": "v"}, res.choice)))

        client.maintenance(verifier=verify_fn, requirements=req, engine="exact")
        for i in range(100):
            res = client.decide(
                site=site,
                state={"k": "v"},
                task_id=f"s-{i}",
                fallback=lambda: "a",
            )
            client.record_outcome(res.decision_id, **asdict(verify_fn({"k": "v"}, res.choice)))
        client.maintenance(verifier=verify_fn, requirements=req, engine="exact")

        art = client._artifact(site.version)
        assert art["status"] == "ACTIVE"

        # Under normal conditions, should be fast_path or comparison
        res_normal = client.decide(
            site=site, state={"k": "v"}, task_id="test-normal", fallback=lambda: "a"
        )
        assert res_normal.source == "fast_path" or res_normal.fallback_reason == "comparison"

        # Now mock get_serving_policy to raise an unexpected database failure
        orig_get_policy = client.store.get_serving_policy

        def broken_policy(site_version):
            raise sqlite3.OperationalError("disk I/O error reading serving policy")

        client.store.get_serving_policy = broken_policy
        try:
            res_fail = client.decide(
                site=site, state={"k": "v"}, task_id="test-fail", fallback=lambda: "a"
            )
            # Must fall safe to Host with reason "serving_policy_unavailable"
            assert res_fail.source == "fallback"
            assert res_fail.fallback_reason == "serving_policy_unavailable"
        finally:
            client.store.get_serving_policy = orig_get_policy


def test_server_origin_port_validation_and_decision_redaction(tmp_path):
    """Test strict Origin port checking and default privacy redaction on /api/decisions."""
    site = DecisionSite(
        "secure.redact.site", {"token": "string", "query": "string"}, ("ok", "fail")
    )
    with Ink(tmp_path / "secure_ui.db") as client:
        client.register(site)
        dec = client.decide(
            site=site,
            state={"token": "super_secret_api_key_12345", "query": "hello"},
            task_id="task-redact-1",
            fallback=lambda: "ok",
        )
        client.record_outcome(
            dec.decision_id,
            quality=1.0,
            verifier="manual",
            verifier_version="1",
            evidence={"auth_token": "secret_receipt_token"},
        )

        server = start_server(client, host="127.0.0.1", port=4247, open_browser=False, background=True)
        port = server.server_address[1]
        base_url = f"http://127.0.0.1:{port}"

        try:
            # 1. Origin port mismatch MUST return 403 Forbidden
            req_mismatch = urllib.request.Request(
                f"{base_url}/api/overview",
                headers={
                    "Host": f"127.0.0.1:{port}",
                    "Origin": "http://127.0.0.1:9999",  # Different port!
                },
            )
            with pytest.raises(urllib.error.HTTPError) as exc_mismatch:
                urllib.request.urlopen(req_mismatch, timeout=5)
            assert exc_mismatch.value.code == 403

            # 2. Origin matching port MUST succeed
            req_match = urllib.request.Request(
                f"{base_url}/api/overview",
                headers={
                    "Host": f"127.0.0.1:{port}",
                    "Origin": f"http://127.0.0.1:{port}",
                },
            )
            with urllib.request.urlopen(req_match, timeout=5) as resp:
                assert resp.status == 200

            # 3. Default GET /api/decisions MUST redact sensitive fields
            req_decisions = urllib.request.Request(
                f"{base_url}/api/decisions",
                headers={"Host": f"127.0.0.1:{port}"},
            )
            with urllib.request.urlopen(req_decisions, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                assert len(data["decisions"]) >= 1
                first = data["decisions"][0]
                assert first["state"]["token"] == "[REDACTED]"
                assert first["state"]["query"] == "hello"

            # 4. Opt-in unredacted GET /api/decisions?raw=1 exposes raw state
            req_raw = urllib.request.Request(
                f"{base_url}/api/decisions?raw=1",
                headers={"Host": f"127.0.0.1:{port}"},
            )
            with urllib.request.urlopen(req_raw, timeout=5) as resp:
                data_raw = json.loads(resp.read().decode())
                first_raw = data_raw["decisions"][0]
                assert first_raw["state"]["token"] == "super_secret_api_key_12345"

            # 5. Default GET /api/decisions/<id> MUST redact state and outcome
            req_single = urllib.request.Request(
                f"{base_url}/api/decisions/{dec.decision_id}",
                headers={"Host": f"127.0.0.1:{port}"},
            )
            with urllib.request.urlopen(req_single, timeout=5) as resp:
                single_data = json.loads(resp.read().decode())
                assert single_data["state"]["token"] == "[REDACTED]"
                assert single_data["state"]["query"] == "hello"
                assert single_data["outcome"]["evidence"]["auth_token"] == "[REDACTED]"

            # 6. Opt-in unredacted GET /api/decisions/<id>?unredacted=1 exposes raw data
            req_single_raw = urllib.request.Request(
                f"{base_url}/api/decisions/{dec.decision_id}?unredacted=1",
                headers={"Host": f"127.0.0.1:{port}"},
            )
            with urllib.request.urlopen(req_single_raw, timeout=5) as resp:
                single_raw_data = json.loads(resp.read().decode())
                assert single_raw_data["state"]["token"] == "super_secret_api_key_12345"
                assert single_raw_data["outcome"]["evidence"]["auth_token"] == "secret_receipt_token"

        finally:
            server.shutdown()
            server.server_close()


def test_unknown_tasks_prevent_promotion():
    """Verify that unknown tasks (missing/unverified disagreements) contribute 0.0 and block promotion."""
    from ink.internal.verification import EvidenceStatus, passes, statistics

    # Create 30 records across 30 tasks:
    # 20 tasks are verified positive (quality 1.0)
    # 10 tasks have UNVERIFIED_DISAGREEMENT or UNVERIFIABLE
    records = []
    for i in range(20):
        records.append({
            "task": f"task-pos-{i}",
            "status": EvidenceStatus.VERIFIED_SUCCESS,
            "candidate": {"quality": 1.0},
            "baseline": {"quality": 1.0},
            "agreement": 1.0,
        })
    for i in range(10):
        records.append({
            "task": f"task-unk-{i}",
            "status": EvidenceStatus.UNVERIFIED_DISAGREEMENT,
            "candidate": {"quality": 1.0},  # unverified candidate claim
            "baseline": {"quality": 1.0},
            "agreement": 0.0,
        })

    stats = statistics(records)
    assert stats["samples"] == 30
    assert stats["unknown"] == 10
    assert stats["observed_positive"] == 20
    assert stats["observed_negative"] == 0

    # With 10 unknown tasks contributing 0.0, the effective lower bound is severely penalized:
    # effective candidate vector has 20 ones and 10 zeros: effective mean is 20/30 = 0.667
    # lower bound with Hoeffding penalty: 0.667 - sqrt(ln(20)/(2*30)) = 0.667 - 0.223 = ~0.443
    assert stats["quality_lower"] < 0.50

    # Minimum quality requirement of 0.70 must fail
    req = PromotionRequirements(
        min_samples=25,
        min_quality=0.70,
        min_confidence=0.80,
        max_degradation=0.05,
        min_region_samples=5,
    )
    assert not passes(stats, req)



