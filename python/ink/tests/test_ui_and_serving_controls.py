"""Tests for the local UI Console, Decision Explorer, Replay, and Governed Serving Controls."""

import json
import urllib.request
import urllib.error
from dataclasses import asdict

import pytest

from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements
from ink.internal.contracts import ServingMode
from ink.ui.server import redact_payload, start_server


def test_redact_payload():
    """Verify that sensitive keys are masked while benign fields remain intact."""
    data = {
        "user_id": 12345,
        "auth_token": "sk-secret-abcdef",
        "api_key": "1234-5678",
        "user_email": "alice@example.com",
        "nested": {
            "password_hash": "xyz",
            "normal_field": "hello",
        },
        "query": "balance inquiry",
    }
    redacted = redact_payload(data)
    assert redacted["user_id"] == 12345
    assert redacted["query"] == "balance inquiry"
    assert redacted["auth_token"] == "[REDACTED]"
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["user_email"] == "[REDACTED]"
    assert redacted["nested"]["password_hash"] == "[REDACTED]"
    assert redacted["nested"]["normal_field"] == "hello"


def test_query_decisions_pagination_and_filtering(tmp_path):
    """Verify paginated decision history query and multi-filter criteria."""
    site = DecisionSite("test.explorer.site", {"task_type": "string"}, ("search", "escalate"))
    with Ink(tmp_path / "explorer.db") as client:
        client.register(site)

        # Insert 15 decisions: 10 search, 5 escalate
        for i in range(15):
            choice = "search" if i < 10 else "escalate"
            res = client.decide(
                site=site,
                state={"task_type": f"task_{i}"},
                fallback=lambda c=choice: FallbackResult(c, cost=0.001),
            )
            # Record outcome for first 8 only
            if i < 8:
                client.record_outcome(
                    res.decision_id,
                    quality=1.0,
                    verifier="auto_verifier",
                    verifier_version="v1",
                    evidence={"step": i},
                )

        # 1. Page 1 (limit 5, offset 0)
        rows, total = client.query_decisions(site=site, limit=5, offset=0)
        assert total == 15
        assert len(rows) == 5

        # 2. Page 2 (limit 5, offset 5)
        rows_p2, total_p2 = client.query_decisions(site=site, limit=5, offset=5)
        assert total_p2 == 15
        assert len(rows_p2) == 5
        assert rows[0]["id"] != rows_p2[0]["id"]

        # 3. Filter by has_outcome=True
        with_outcome, total_out = client.query_decisions(site=site, has_outcome=True)
        assert total_out == 8
        assert len(with_outcome) == 8

        # 4. Filter by has_outcome=False
        without_outcome, total_no_out = client.query_decisions(site=site, has_outcome=False)
        assert total_no_out == 7
        assert len(without_outcome) == 7


def test_serving_policy_governance_invariants(tmp_path):
    """Serving authority rules:

    1. Unqualified candidate CANNOT be set to ACTIVE or CANARY.
    2. HOST_OVERRIDE and SHADOW can always be set (safety stop).
    3. Once qualified, CANARY and ACTIVE can be governed with stable assignment.
    """
    site = DecisionSite("test.serving.policy", {"refund": "boolean"}, ("allow", "deny"))
    req = PromotionRequirements(10, 0.5, 0.5, 0.6, 0.25, 5, 100)

    with Ink(tmp_path / "policy.db") as client:
        client.register(site)

        # 1. Unqualified site: setting ACTIVE or CANARY must fail
        with pytest.raises(ValueError, match="no qualified ACTIVE artifact"):
            client.set_serving_policy(site, mode="ACTIVE")

        with pytest.raises(ValueError, match="no qualified ACTIVE artifact"):
            client.set_serving_policy(site, mode="CANARY", canary_rate=0.10)

        # 2. Setting SHADOW or HOST_OVERRIDE is allowed even when unqualified
        p_shadow = client.set_serving_policy(site, mode="SHADOW")
        assert p_shadow["mode"] == "SHADOW"

        p_host = client.set_serving_policy(site, mode="HOST_OVERRIDE")
        assert p_host["mode"] == "HOST_OVERRIDE"

        def verify_fn(s, c):
            exp = "allow" if s.get("refund") else "deny"
            return Outcome(float(c == exp), "verifier", "1", {"expected": exp})

        def feed(count, prefix):
            for i in range(count):
                st = {"refund": i % 2 == 0}
                exp = "allow" if st["refund"] else "deny"
                res = client.decide(
                    site=site,
                    state=st,
                    task_id=f"{prefix}-{i}",
                    fallback=lambda e=exp: FallbackResult(e, model_calls=1),
                )
                client.record_outcome(res.decision_id, **asdict(verify_fn(st, res.choice)))

        # Feed observations and qualify via maintenance
        feed(250, "obs")
        client.maintenance(verifier=verify_fn, requirements=req, engine="exact")
        feed(100, "shadow")
        client.maintenance(verifier=verify_fn, requirements=req, engine="exact")

        art = client._artifact(site.version)
        assert art["status"] == "ACTIVE"

        # 3. Now setting CANARY or ACTIVE succeeds
        p_canary = client.set_serving_policy(site, mode="CANARY", canary_rate=0.0)
        assert p_canary["mode"] == "CANARY"

        # When canary_rate = 0.0, all traffic gets canary_holdout
        res_canary = client.decide(
            site=site,
            state={"refund": True},
            fallback=lambda: "allow",
        )
        assert res_canary.source == "fallback"
        assert res_canary.fallback_reason == "canary_holdout"

        # When set to HOST_OVERRIDE, emergency fallback is strictly triggered
        client.set_serving_policy(site, mode="HOST_OVERRIDE")
        res_override = client.decide(
            site=site,
            state={"refund": True},
            fallback=lambda: "allow",
        )
        assert res_override.source == "fallback"
        assert res_override.fallback_reason == "host_override"

        # When set back to ACTIVE 100%, fast-path serves (<0.1ms)
        client.set_serving_policy(site, mode="ACTIVE", canary_rate=1.0)
        res_active = client.decide(
            site=site,
            state={"refund": True},
            fallback=lambda: "allow",
        )
        assert res_active.source == "fast_path" or res_active.fallback_reason == "comparison"
        assert res_active.fallback_reason not in ("canary_holdout", "host_override")


def test_ui_http_server_endpoints(tmp_path):
    """Test the local HTTP server API endpoints."""
    site = DecisionSite(
        "test.ui.api",
        {"flag": "boolean", "secret_key": "string"},
        ("true_choice", "false_choice"),
    )
    with Ink(tmp_path / "ui_test.db") as client:
        client.register(site)
        res = client.decide(
            site=site,
            state={"flag": True, "secret_key": "topsecret"},
            fallback=lambda: FallbackResult("true_choice", cost=0.005),
        )
        client.record_outcome(
            res.decision_id, quality=1.0, verifier="test_ver", verifier_version="1", evidence={}
        )

        server = start_server(
            client, host="127.0.0.1", port=4243, open_browser=False, background=True
        )
        try:
            base_url = "http://127.0.0.1:4243"

            # 1. Health check
            with urllib.request.urlopen(f"{base_url}/api/health") as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode())
                assert data["status"] == "ok"

            # 2. Overview
            with urllib.request.urlopen(f"{base_url}/api/overview") as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode())
                assert data["sites_count"] == 1
                assert data["total_decisions"] == 1

            # 3. Sites list
            with urllib.request.urlopen(f"{base_url}/api/sites") as resp:
                assert resp.status == 200
                sites = json.loads(resp.read().decode())
                assert len(sites) == 1
                assert sites[0]["name"] == "test.ui.api"
                assert "serving_policy" in sites[0]

            # 4. Decisions list with redaction
            with urllib.request.urlopen(f"{base_url}/api/decisions?redact=1") as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode())
                assert data["total"] == 1
                dec = data["decisions"][0]
                assert dec["state"]["secret_key"] == "[REDACTED]"
                assert dec["state"]["flag"] is True

            # 5. Policy mutations over HTTP are disabled in v0.7.0 (strictly read-only HTTP console)
            req = urllib.request.Request(
                f"{base_url}/api/sites/test.ui.api/policy",
                data=json.dumps({"mode": "ACTIVE"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 405

            # /api/auth/token endpoint must not exist (returns 404)
            req_tok = urllib.request.Request(f"{base_url}/api/auth/token")
            with pytest.raises(urllib.error.HTTPError) as exc_tok:
                urllib.request.urlopen(req_tok)
            assert exc_tok.value.code == 404

            # Programmatic serving policy management via Python client works as designed
            policy = client.get_serving_policy("test.ui.api")
            assert policy["mode"] == "ACTIVE"
            assert policy["canary_rate"] == 1.0

            # 6. Serving HTML index
            with urllib.request.urlopen(f"{base_url}/") as resp:
                assert resp.status == 200
                content = resp.read().decode()
                assert "Ink Console" in content
                assert "Decision Trace Inspector" in content

        finally:
            server.shutdown()
            server.server_close()
