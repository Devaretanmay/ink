"""Task 1: decision primitive, fallback semantics, compatibility, and lifecycle."""

import asyncio
import json
import sqlite3
from dataclasses import asdict

import pytest
from microloop import DecisionSite, FallbackResult, Microloop, Outcome, PromotionRequirements
from microloop.internal.contracts import canonical
from microloop.internal.decision_store import SCHEMA_VERSION, DecisionStore, split_history

SITE = DecisionSite("ticket.action", {"refund": "boolean"}, ("refund", "specialist"))
REQ = PromotionRequirements(10, 0.5, 0.5, 0.6, 0.25, 5, 100)


def verify(state, choice):
    expected = "refund" if state["refund"] else "specialist"
    return Outcome(float(choice == expected), "ledger", "1", {"expected": expected})


def run(client, count, prefix="task", bad=False):
    results = []
    for i in range(count):
        state = {"refund": i % 2 == 0}
        result = client.decide(
            site=SITE,
            state=state,
            task_id=f"{prefix}-{i}",
            fallback=lambda state=state: FallbackResult(
                "refund" if state["refund"] else "specialist", model_calls=1
            ),
        )
        outcome = verify(state, result.choice)
        client.record_outcome(
            result.decision_id,
            **(asdict(outcome) if not bad else {**asdict(outcome), "quality": 0.0}),
        )
        results.append(result)
    return results


def activate(client):
    run(client, 300, "observe")
    client.compile(SITE, engine="exact")
    client.calibrate(SITE, verifier=verify, requirements=REQ)
    run(client, 100, "shadow")
    assert client.evaluate(SITE, verifier=verify)["qualified"]


def test_fallback_contract_and_outcomes(tmp_path):
    with Microloop(tmp_path / "history.db") as client:
        result = client.decide(site=SITE, state={"refund": True}, fallback=lambda: "refund")
        assert result.source == "fallback"
        assert result.fallback_reason == "observe"
        assert client.inspect(SITE)["outcome_completeness"] == 0
        data = asdict(verify({"refund": True}, "refund"))
        client.record_outcome(result.decision_id, **data)
        client.record_outcome(result.decision_id, **data)
        with pytest.raises(ValueError, match="Conflicting"):
            client.record_outcome(result.decision_id, **{**data, "quality": 0})
        with pytest.raises(sqlite3.IntegrityError):
            client.record_outcome("missing", **data)
        with pytest.raises(ValueError):
            client.decide(site=SITE, state={"refund": 1}, fallback=lambda: "refund")
        with pytest.raises(ValueError):
            client.decide(site=SITE, state={"refund": True}, fallback=lambda: "other")


def test_exceptions_and_async_cancellation():
    with Microloop(":memory:") as client:
        error = RuntimeError("original")

        def fail():
            raise error

        with pytest.raises(RuntimeError) as caught:
            client.decide(site=SITE, state={"refund": True}, fallback=fail)
        assert caught.value is error

        async def scenario():
            async def good():
                return "refund"

            assert (
                await client.decide_async(site=SITE, state={"refund": True}, fallback=good)
            ).choice == "refund"

            async def cancel():
                raise asyncio.CancelledError()

            with pytest.raises(asyncio.CancelledError):
                await client.decide_async(site=SITE, state={"refund": True}, fallback=cancel)

        asyncio.run(scenario())


def test_lifecycle_restart_corruption_and_demotion(tmp_path):
    path = tmp_path / "history.db"
    with Microloop(path) as client:
        activate(client)
    with Microloop(path) as client:
        results = run(client, 100, "active")
        assert any(r.source == "fast_path" for r in results)
        assert any(r.fallback_reason == "comparison" for r in results)
        assert client.inspect(SITE)["state"] == "ACTIVE"
        run(client, 100, "drift", bad=True)
        assert client.reevaluate(SITE)["demoted"]
        assert client.inspect(SITE)["state"] == "SHADOW"
        assert run(client, 1, "after")[0].source == "fallback"
        with pytest.raises(ValueError, match="fresh shadow"):
            client.evaluate(SITE, verifier=verify)
        with client.store.transaction() as db:
            db.execute("UPDATE artifacts SET checksum='corrupt'")
        result = run(client, 1, "corrupt")[0]
        assert result.fallback_reason == "engine_or_store_unavailable"


def test_missing_outcomes_and_unqualified_profile():
    with Microloop(":memory:") as client:
        run(client, 300)
        client.compile(SITE, engine="exact")
        with pytest.raises(ValueError, match="calibrated"):
            client.evaluate(SITE, verifier=verify)
        client.calibrate(SITE, verifier=verify, requirements=REQ)
        for _ in range(100):
            client.decide(site=SITE, state={"refund": True}, fallback=lambda: "refund")
        with pytest.raises(ValueError, match="fresh shadow"):
            client.evaluate(SITE, verifier=verify)
        assert client.inspect(SITE)["state"] == "SHADOW"


def test_disagreement_does_not_inherit_good_outcome():
    with Microloop(":memory:") as client:
        run(client, 300)
        client.compile(SITE, engine="exact")

        def bad_candidate(state, choice):
            return Outcome(0, "independent", "1", {"executed": choice})

        with pytest.raises(ValueError, match="No state regions"):
            client.calibrate(SITE, verifier=bad_candidate, requirements=REQ)


def test_version_and_novel_state():
    with Microloop(":memory:") as client:
        activate(client)
        changed = DecisionSite(SITE.name, SITE.state_schema, SITE.choices, "2")
        r = client.decide(site=changed, state={"refund": True}, fallback=lambda: "specialist")
        assert r.source == "fallback" and r.site_version != SITE.version
        numeric = DecisionSite("number", {"amount": "number"}, ("yes", "no"))
        assert numeric.encode({"amount": 1}) == numeric.encode({"amount": 1.0})
        with pytest.raises(ValueError):
            numeric.encode({"amount": float("nan")})


def test_grouped_split_and_retention(tmp_path):
    rows = [{"task": str(i % 10), "id": i} for i in range(100)]
    partitions = split_history(rows)
    groups = [{r["task"] for r in part} for part in partitions]
    assert not groups[0] & groups[1] and not groups[1] & groups[2]
    with Microloop(tmp_path / "db") as client:
        run(client, 20)
        client.store.export(tmp_path / "export.json")
        assert len(json.loads((tmp_path / "export.json").read_text())["tables"]["decisions"]) == 20
        assert client.store.retain_since(1e20) == 20
        assert not client.store.history(SITE.version)


def test_transactions_and_concurrent_connections(tmp_path):
    path = tmp_path / "db"
    with Microloop(path) as a, Microloop(path) as b:
        run(a, 2, "a")
        run(b, 2, "b")
        assert a.inspect(SITE)["observations"] == 4
        with pytest.raises(RuntimeError), a.store.transaction() as db:
            db.execute("DELETE FROM outcomes")
            raise RuntimeError("interrupted")
        assert a.inspect(SITE)["outcome_completeness"] == 1
    db = DecisionStore(path)
    assert db.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
    db.close()


def test_cli(tmp_path, capsys):
    from microloop.cli import main

    path = tmp_path / "db"
    with Microloop(path) as client:
        run(client, 2)
    assert main(["sites", "--db", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["name"] == SITE.name
    assert main(["inspect", SITE.name, "--db", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["observations"] == 2


def test_engine_failure_and_immutable_artifact():
    with Microloop(":memory:") as client:
        activate(client)
        before = client._artifact(SITE.version)["checksum"]

        class Broken:
            def predict(self, *args):
                raise RuntimeError("engine is down")

        client.engines["exact"] = Broken()
        result = run(client, 1, "broken")[0]
        assert result.source == "fallback"
        assert client._artifact(SITE.version)["checksum"] == before
        assert canonical({"b": 1, "a": 2}) == canonical({"a": 2, "b": 1})


def test_storage_failure_falls_back_once(tmp_path, monkeypatch):
    from contextlib import contextmanager

    with Microloop(tmp_path / "outage.db") as client:
        activate(client)
        calls = []

        @contextmanager
        def unavailable():
            raise sqlite3.OperationalError("disk full")
            yield  # pragma: no cover

        monkeypatch.setattr(client.store, "transaction", unavailable)
        result = client.decide(
            site=SITE, state={"refund": True}, fallback=lambda: calls.append(True) or "refund"
        )
        assert calls == [True]
        assert result.source == "fallback" and not result.recorded


def test_fixed_call_metrics_require_measured_usage():
    from dataclasses import replace

    site = replace(SITE, fallback_model_calls=1)
    with Microloop(":memory:") as client:
        with pytest.raises(ValueError, match="fixed model-call"):
            client.decide(site=site, state={"refund": True}, fallback=lambda: "refund")
        client.decide(
            site=site,
            state={"refund": True},
            fallback=lambda: FallbackResult("refund", model_calls=1),
        )
        assert client.inspect(site)["model_calls_avoided"] == 0
        assert client.inspect(site)["usage"]["model_calls"] == 1


def test_recompile_preserves_old_evidence():
    with Microloop(":memory:") as client:
        activate(client)
        old = client.inspect(SITE)["fast_path"]
        new = client.compile(SITE, replace_existing=True, engine="exact")
        assert old != new
        assert client.inspect(SITE)["state"] == "SHADOW"
        old_row = client.store.rows("SELECT * FROM artifacts WHERE id=?", (old,))[0]
        assert old_row["status"] == "RETIRED" and old_row["evidence"]


def test_async_inference_does_not_block_event_loop(monkeypatch):
    import time

    with Microloop(":memory:") as client:
        calls = []

        def slow(*args):
            time.sleep(0.05)
            return None, None, "observe"

        monkeypatch.setattr(client, "_route", slow)

        async def scenario():
            async def fallback():
                return "refund"

            async def ticker():
                await asyncio.sleep(0.01)
                calls.append("tick")

            task = asyncio.create_task(ticker())
            await client.decide_async(site=SITE, state={"refund": True}, fallback=fallback)
            assert calls == ["tick"]
            await task

        asyncio.run(scenario())


def test_contract_cannot_mutate_after_registration():
    site = DecisionSite("immutable", {"amount": "number?"}, ("yes", "no"))
    before = site.version
    with pytest.raises(TypeError):
        site.state_schema["amount"] = "string"
    assert site.encode({}) == {"amount": None}
    assert site.version == before
    with pytest.raises(ValueError):
        DecisionSite("bad", {"amount": "number??"}, ("yes", "no"))
