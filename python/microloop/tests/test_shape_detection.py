"""Tests for automatic decision shape detection, volatile fields, templates, and CLI."""

from __future__ import annotations

import json

from microloop.cli import main as cli_main
from microloop.decision_api import DecisionSite, FallbackResult, Microloop
from microloop.discovery import (
    assess_verifier_readiness,
    detect_templates,
    detect_volatile_fields,
    generate_snippet,
    infer_state_schema,
)


def test_detect_volatile_fields():
    states = [
        {
            "session_id": f"sess_{i}",
            "request_uuid": f"12345678-1234-5678-1234-56781234567{i % 10}",
            "intent": "refund",
            "tier": "enterprise",
        }
        for i in range(50)
    ]
    volatile = detect_volatile_fields(states)
    assert "session_id" in volatile
    assert "request_uuid" in volatile
    assert "intent" not in volatile
    assert "tier" not in volatile


def test_infer_state_schema():
    states = [
        {
            "session_id": f"sess_{i}",
            "query": "return item",
            "count": 2,
            "amount": 29.99,
            "is_vip": True,
            "metadata": {"source": "web"},
            "tags": ["urgent"],
        }
        for i in range(20)
    ]
    schema = infer_state_schema(states, exclude_fields={"session_id"})
    assert "session_id" not in schema
    assert schema["query"] == "string"
    assert schema["count"] == "integer"
    assert schema["amount"] == "float"
    assert schema["is_vip"] == "boolean"
    assert schema["metadata"] == "object"
    assert schema["tags"] == "array"


def test_detect_templates():
    states = [{"query": f"Retrieve order {1000 + i} status"} for i in range(50)]
    raw_rep, templated_rep = detect_templates(states)
    assert raw_rep == 0.0
    assert templated_rep > 0.90


def test_assess_verifier_readiness():
    from microloop.discovery import CanonicalTrace

    # Ready site
    ready_rows = [
        CanonicalTrace(
            timestamp=1.0,
            callsite="site_a",
            state={"q": "a"},
            choices=["x"],
            choice="x",
            latency_ms=10.0,
            cost_usd=0.001,
            model="m",
            outcome={"quality": 1.0, "verifier": "db_check"},
        )
        for _ in range(20)
    ]
    readiness, cov, verifiers = assess_verifier_readiness(ready_rows)
    assert readiness == "verifier_ready"
    assert cov == 1.0
    assert "db_check" in verifiers

    # Unverified site
    unverified_rows = [
        CanonicalTrace(
            timestamp=1.0,
            callsite="site_b",
            state={"q": "b"},
            choices=["y"],
            choice="y",
            latency_ms=10.0,
            cost_usd=0.001,
            model="m",
            outcome=None,
        )
        for _ in range(20)
    ]
    readiness_u, cov_u, _ = assess_verifier_readiness(unverified_rows)
    assert readiness_u == "no_verifier"
    assert cov_u == 0.0


def test_generate_snippet():
    snippet = generate_snippet("support.route", {"text": "string"}, ["refund", "escalate"])
    assert 'name="support.route"' in snippet
    assert '"text": "string"' in snippet
    assert "'refund', 'escalate'" in snippet
    assert "ml.decide" in snippet
    assert "ml.record_outcome" in snippet

    # Snippet with suggested volatile exclusions
    vol_snippet = generate_snippet(
        "support.route",
        {"text": "string"},
        ["refund", "escalate"],
        volatile_fields=["session_id", "timestamp"],
    )
    assert "Suggested exclusions detected in telemetry" in vol_snippet
    assert "session_id" in vol_snippet
    assert "timestamp" in vol_snippet


def test_explicit_invalidation(tmp_path):
    from microloop import Outcome, PromotionRequirements
    db_path = tmp_path / "invalidation_test.db"
    site = DecisionSite("inval_site", {"cat": "string"}, ("a", "b"))
    with Microloop(str(db_path)) as client:
        client.register(site)
        for i in range(120):
            res = client.decide(
                site=site.name,
                state={"cat": f"c_{i % 2}"},
                fallback=lambda i=i: "a" if (i % 2 == 0) else "b",
            )
            client.record_outcome(
                decision_id=res.decision_id,
                quality=1.0,
                verifier="test_v",
                verifier_version="1",
                evidence={"ok": True},
            )
        req = PromotionRequirements(6, 0.5, 0.5, 0.75, 0.25, 3, 50)
        client.compile(site.name, engine="exact")
        def verifier(s, c):
            return Outcome(1.0, "test_v", "1", {"ok": True})
        client.calibrate(site.name, verifier=verifier, requirements=req)
        for i in range(50):
            res = client.decide(
                site=site.name,
                state={"cat": f"c_{i % 2}"},
                fallback=lambda i=i: "a" if (i % 2 == 0) else "b",
            )
            client.record_outcome(
                decision_id=res.decision_id,
                quality=1.0,
                verifier="test_v",
                verifier_version="1",
                evidence={"ok": True},
            )
        eval_res = client.evaluate(site.name, verifier=verifier, auto_promote=True)
        assert eval_res["qualified"]
        assert client.inspect(site.name)["state"] == "ACTIVE"

        # Explicit invalidation: demote to SHADOW
        inval_res = client.invalidate(site.name, reason="policy_v2_migration")
        assert inval_res["invalidated"] is True
        assert inval_res["previous_status"] == "ACTIVE"
        assert inval_res["new_status"] == "SHADOW"
        assert client.inspect(site.name)["state"] == "SHADOW"



def test_cli_discover_and_value(tmp_path, capsys):
    traces = [
        {
            "callsite": "support.route",
            "state": {"intent": f"cat_{i % 3}"},
            "choice": "refund" if (i % 3 == 0) else "agent",
            "outcome": {"quality": 1.0, "verifier": "fulfillment"},
            "latency_ms": 120.0,
            "cost_usd": 0.002,
            "timestamp": 1000 + i * 10,
        }
        for i in range(60)
    ]
    trace_path = tmp_path / "traces.jsonl"
    with open(trace_path, "w", encoding="utf-8") as f:
        for t in traces:
            f.write(json.dumps(t) + "\n")

    # 1. microloop discover traces.jsonl
    exit_code = cli_main(["discover", str(trace_path)])
    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "Found 1 candidate call sites." in captured
    assert "support.route" in captured
    assert "STRONG CANDIDATE" in captured

    # 2. microloop discover traces.jsonl --json --profile --snippet
    exit_code = cli_main(["discover", str(trace_path), "--json", "--profile", "--snippet"])
    assert exit_code == 0
    captured_json = capsys.readouterr().out
    parsed = json.loads(captured_json)
    assert len(parsed) == 1
    assert parsed[0]["site_name"] == "support.route"
    assert parsed[0]["recommendation"] == "compile"
    assert "DecisionSite(" in parsed[0]["snippet"]

    # 3. microloop value on active database
    db_path = tmp_path / "test_decisions.db"
    site = DecisionSite("demo_site", {"cat": "string"}, ("a", "b"))
    with Microloop(str(db_path)) as client:
        client.register(site)
        for i in range(10):
            res = client.decide(
                site=site.name,
                state={"cat": f"c_{i}"},
                fallback=lambda: FallbackResult("a", model_calls=1, cost=0.002),
            )
            client.record_outcome(
                decision_id=res.decision_id,
                quality=1.0,
                verifier="test_verifier",
                verifier_version="1",
                evidence={"test": True},
            )

    exit_code = cli_main(["value", "--db", str(db_path)])
    assert exit_code == 0
    val_out = capsys.readouterr().out
    assert "MICROLOOP VALUE REPORT" in val_out
    assert "Registered sites      : 1" in val_out

    exit_code = cli_main(["value", "--db", str(db_path), "--json"])
    assert exit_code == 0
    val_json = json.loads(capsys.readouterr().out)
    assert val_json["registered_sites"] == 1
    assert "model_calls_avoided" in val_json
