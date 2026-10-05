import asyncio
import concurrent.futures
import multiprocessing as mp
import sqlite3
import time
from dataclasses import asdict
from unittest.mock import patch

import pytest
from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements


class HostCustomException(Exception):
    pass


def _mp_worker(db_path, n_ops, results_queue):
    errors = []
    site = DecisionSite("worker.site", {"id": "integer"}, ("allow", "deny"))
    try:
        with Ink(db_path, timeout=2.0) as loop:
            for i in range(n_ops):
                try:
                    res = loop.decide(
                        site=site,
                        state={"id": i % 3},
                        fallback=lambda: FallbackResult("allow", cost=0.001, model_calls=1),
                    )
                    saved = loop.record_outcome(
                        res.decision_id,
                        quality=1.0,
                        verifier="test",
                        verifier_version="1",
                        evidence={"i": i},
                    )
                    if res.recorded and saved is None:
                        errors.append(("OutcomeNotSaved", f"op {i}"))
                except Exception as e:
                    errors.append((type(e).__name__, str(e)))
    except Exception as e:
        errors.append(("InitError", str(e)))
    results_queue.put(errors)


def test_concurrent_threads_decide_and_outcomes(tmp_path):
    db_path = str(tmp_path / "threads.db")
    site = DecisionSite("threads.site", {"k": "integer"}, ("allow", "deny"))
    n_workers = 8
    n_calls = 30

    with Ink(db_path) as loop:
        loop.register(site)

        def worker(w_id):
            errs = []
            for i in range(n_calls):
                try:
                    res = loop.decide(
                        site=site,
                        state={"k": i % 5},
                        fallback=lambda: FallbackResult("allow", cost=0.001, model_calls=1),
                    )
                    saved = loop.record_outcome(
                        res.decision_id,
                        quality=1.0,
                        verifier="test",
                        verifier_version="1",
                        evidence={"w": w_id, "i": i},
                    )
                    if res.recorded and saved is None:
                        errs.append(("OutcomeNotSaved", f"w={w_id}, i={i}"))
                except Exception as e:
                    errs.append((type(e).__name__, str(e)))
            return errs

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as executor:
            futs = [executor.submit(worker, i) for i in range(n_workers)]
            all_errs = [e for f in futs for e in f.result()]

    assert all_errs == []
    with Ink(db_path, readonly=True) as loop:
        history = loop.store.history(site.version)
        assert len(history) == n_workers * n_calls


def test_concurrent_processes_multiprocessing(tmp_path):
    db_path = str(tmp_path / "procs.db")
    site = DecisionSite("worker.site", {"id": "integer"}, ("allow", "deny"))
    with Ink(db_path) as loop:
        loop.register(site)

    n_procs = 4
    n_ops = 25
    q = mp.Queue()
    procs = [mp.Process(target=_mp_worker, args=(db_path, n_ops, q)) for _ in range(n_procs)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=20)

    errors = []
    while not q.empty():
        errors.extend(q.get())

    assert errors == []
    with Ink(db_path, readonly=True) as loop:
        history = loop.store.history(site.version)
        assert len(history) == n_procs * n_ops


def test_forced_lock_fail_open_decide_and_outcome(tmp_path):
    db_path = str(tmp_path / "lock.db")
    site = DecisionSite("lock.site", {"k": "integer"}, ("allow", "deny"))

    with Ink(db_path, timeout=0.2) as loop:
        initial = loop.decide(
            site=site,
            state={"k": 1},
            fallback=lambda: FallbackResult("allow", cost=0.001, model_calls=1),
        )
        assert initial.recorded

        # Hold exclusive lock on another connection.
        conn_lock = sqlite3.connect(db_path, timeout=0.1)
        conn_lock.execute("BEGIN EXCLUSIVE")
        try:
            t0 = time.perf_counter()
            res = loop.decide(
                site=site,
                state={"k": 2},
                fallback=lambda: FallbackResult("deny", cost=0.001, model_calls=1),
            )
            elapsed = time.perf_counter() - t0
            assert elapsed < 2.0
            assert res.choice == "deny"
            assert res.source == "fallback"
            assert not res.recorded
            assert res.decision_id is None
            assert res.fallback_reason in ("storage_locked", "storage_unavailable")

            # Outcome write during lock must return None rather than raising.
            saved = loop.record_outcome(
                initial.decision_id,
                quality=1.0,
                verifier="test",
                verifier_version="1",
                evidence={"ok": 1},
            )
            assert saved is None
        finally:
            conn_lock.rollback()
            conn_lock.close()


def test_unrecorded_and_missing_decision_outcomes(tmp_path):
    with Ink(tmp_path / "unrecorded.db") as loop:
        res = loop.record_outcome(
            None, quality=1.0, verifier="test", verifier_version="1", evidence={"ok": 1}
        )
        assert res is None

        missing = loop.record_outcome(
            "nonexistent_uuid",
            quality=1.0,
            verifier="test",
            verifier_version="1",
            evidence={"ok": 1},
        )
        assert missing is None


def test_duplicate_and_conflicting_outcomes(tmp_path):
    site = DecisionSite("outcome.site", {"x": "integer"}, ("allow", "deny"))
    with Ink(tmp_path / "outcomes.db") as loop:
        res = loop.decide(
            site=site,
            state={"x": 10},
            fallback=lambda: FallbackResult("allow", cost=0.001, model_calls=1),
        )
        out1 = loop.record_outcome(
            res.decision_id,
            quality=1.0,
            verifier="test",
            verifier_version="1",
            evidence={"ok": True},
        )
        assert isinstance(out1, Outcome)

        # Duplicate identical outcome is idempotent.
        out2 = loop.record_outcome(
            res.decision_id,
            quality=1.0,
            verifier="test",
            verifier_version="1",
            evidence={"ok": True},
        )
        assert out2 == out1

        # Conflicting outcome raises contract error.
        with pytest.raises(ValueError, match="Conflicting outcome"):
            loop.record_outcome(
                res.decision_id,
                quality=0.0,
                verifier="test",
                verifier_version="1",
                evidence={"ok": True},
            )


def test_contract_violations(tmp_path):
    site = DecisionSite("contract.site", {"k": "integer"}, ("allow", "deny"))
    with Ink(tmp_path / "contract.db") as loop:
        with pytest.raises(ValueError, match="Invalid state field"):
            loop.decide(site=site, state={}, fallback=lambda: "allow")

        with pytest.raises(ValueError, match="only declared fields"):
            loop.decide(site=site, state={"k": 1, "extra": "x"}, fallback=lambda: "allow")

        with pytest.raises(ValueError, match="Invalid state field"):
            loop.decide(site=site, state={"k": "not_an_int"}, fallback=lambda: "allow")

        with pytest.raises(ValueError, match="undeclared choice"):
            loop.decide(site=site, state={"k": 1}, fallback=lambda: "invalid_choice")


def test_fallback_exception_preserved(tmp_path):
    site = DecisionSite("err.site", {"k": "integer"}, ("allow", "deny"))
    with Ink(tmp_path / "err.db") as loop:
        def failing_fallback():
            raise HostCustomException("External payment required")

        with pytest.raises(HostCustomException, match="External payment required"):
            loop.decide(site=site, state={"k": 1}, fallback=failing_fallback)


def test_async_fallback_handling(tmp_path):
    site = DecisionSite("async.site", {"k": "integer"}, ("allow", "deny"))
    with Ink(tmp_path / "async.db") as loop:
        # Sync decide rejects async fallback.
        async def async_fallback():
            return "allow"

        with pytest.raises(TypeError, match="Async fallbacks require decide_async"):
            loop.decide(site=site, state={"k": 1}, fallback=async_fallback)

        async def run_async_tests():
            res_async = await loop.decide_async(site=site, state={"k": 1}, fallback=async_fallback)
            assert res_async.choice == "allow"

            # Sync fallback inside decide_async is accepted seamlessly.
            res_sync = await loop.decide_async(
                site=site, state={"k": 2}, fallback=lambda: "deny"
            )
            assert res_sync.choice == "deny"

        asyncio.run(run_async_tests())


def test_fault_injection_route_and_store_failures(tmp_path):
    site = DecisionSite("inject.site", {"k": "integer"}, ("allow", "deny"))
    with Ink(tmp_path / "inject.db") as loop:
        # Route failure fails open to fallback.
        with patch.object(loop, "_route", side_effect=RuntimeError("internal routing error")):
            res = loop.decide(site=site, state={"k": 1}, fallback=lambda: "allow")
            assert res.choice == "allow"
            assert res.source == "fallback"

        # Fast-path save failure falls open to fallback without crashing.
        with patch.object(loop, "_save", side_effect=sqlite3.OperationalError("disk error")):
            res2 = loop.decide(site=site, state={"k": 2}, fallback=lambda: "deny")
            assert res2.choice == "deny"
            assert res2.source == "fallback"
            assert not res2.recorded


def test_process_restart_and_history_consistency(tmp_path):
    db_path = str(tmp_path / "restart.db")
    site = DecisionSite("restart.site", {"k": "integer"}, ("allow", "deny"))

    with Ink(db_path) as loop:
        res = loop.decide(
            site=site,
            state={"k": 10},
            fallback=lambda: FallbackResult("allow", cost=0.001, model_calls=1),
        )
        loop.record_outcome(
            res.decision_id, quality=1.0, verifier="test", verifier_version="1", evidence={"k": 10}
        )

    # Reopen database fresh.
    with Ink(db_path) as loop2:
        sites = loop2.sites()
        assert len(sites) == 1
        assert sites[0]["name"] == "restart.site"
        assert sites[0]["observations"] == 1
        history = loop2.store.history(site.version)
        assert len(history) == 1
        assert history[0]["choice"] == "allow"
        assert history[0]["outcome"]["quality"] == 1.0


def test_runtime_safety_stress_suite(tmp_path):
    db_path = str(tmp_path / "stress.db")
    site = DecisionSite("stress.site", {"x": "integer"}, ("left", "right"))
    req = PromotionRequirements(
        min_samples=10,
        min_quality=0.5,
        min_confidence=0.5,
        max_degradation=0.8,
        comparison_rate=0.1,
        min_region_samples=5,
        evaluation_window=100,
    )

    def verifier(state, choice):
        exp = "left" if state["x"] % 2 == 0 else "right"
        return Outcome(float(choice == exp), "stress_ver", "1.0", {"exp": exp})

    with Ink(db_path) as loop:
        loop.register(site)
        for i in range(240):
            st = {"x": i % 4}
            exp = "left" if st["x"] % 2 == 0 else "right"
            res = loop.decide(
                site=site,
                state=st,
                task_id=f"init-{i}",
                fallback=lambda exp=exp: FallbackResult(exp, cost=0.001, model_calls=1),
            )
            loop.record_outcome(res.decision_id, **asdict(verifier(st, res.choice)))

        loop.compile(site, engine="exact")
        loop.calibrate(site, verifier=verifier, requirements=req)
        for i in range(100):
            st = {"x": i % 4}
            exp = "left" if st["x"] % 2 == 0 else "right"
            res = loop.decide(
                site=site,
                state=st,
                task_id=f"shad-{i}",
                fallback=lambda exp=exp: FallbackResult(exp, cost=0.001, model_calls=1),
            )
            loop.record_outcome(res.decision_id, **asdict(verifier(st, res.choice)))
        eval_res = loop.evaluate(site, verifier=verifier)
        assert eval_res["qualified"]

        latencies = []
        counts = {
            "total": 0,
            "success": 0,
            "fallback": 0,
            "fast_path": 0,
            "persistence_failures": 0,
            "uncaught_exceptions": 0,
        }

        def stress_worker(w_id):
            local_counts = {
                "success": 0,
                "fallback": 0,
                "fast_path": 0,
                "persist_fail": 0,
                "exc": 0,
            }
            local_lat = []
            for i in range(50):
                st = {"x": (w_id + i) % 6}
                exp = "left" if st["x"] % 2 == 0 else "right"
                t0 = time.perf_counter()
                try:
                    res = loop.decide(
                        site=site,
                        state=st,
                        task_id=f"stress-{w_id}-{i}",
                        fallback=lambda exp=exp: FallbackResult(exp, cost=0.001, model_calls=1),
                    )
                    local_lat.append(time.perf_counter() - t0)
                    local_counts["success"] += 1
                    if res.source == "fast_path":
                        local_counts["fast_path"] += 1
                    else:
                        local_counts["fallback"] += 1
                    if not res.recorded:
                        local_counts["persist_fail"] += 1
                    loop.record_outcome(res.decision_id, **asdict(verifier(st, res.choice)))
                except Exception:
                    local_counts["exc"] += 1
            return local_counts, local_lat

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futs = [executor.submit(stress_worker, i) for i in range(8)]
            for f in futs:
                cnt, lats = f.result()
                counts["total"] += 50
                counts["success"] += cnt["success"]
                counts["fallback"] += cnt["fallback"]
                counts["fast_path"] += cnt["fast_path"]
                counts["persistence_failures"] += cnt["persist_fail"]
                counts["uncaught_exceptions"] += cnt["exc"]
                latencies.extend(lats)

        assert counts["uncaught_exceptions"] == 0
        assert counts["total"] == 400
        assert counts["success"] == 400
        assert counts["fast_path"] > 0
        with loop.store.lock:
            integrity = loop.store.conn.execute("PRAGMA integrity_check").fetchone()[0]
            assert integrity == "ok"


def test_programmer_errors_not_masked(tmp_path):
    db_path = str(tmp_path / "safety_contracts.db")
    site = DecisionSite("safety.contract", {"k": "integer"}, ("allow", "deny"))

    with Ink(db_path) as loop:
        loop.register(site)

        # Developer passing non-dict state raises TypeError and is not swallowed
        with pytest.raises(TypeError, match="State must be a dict"):
            loop.decide(
                site=site,
                state="bad_type_not_dict",
                fallback=lambda: "allow",
            )

        # Developer passing wrong type for field raises ValueError
        with pytest.raises(ValueError, match="Invalid state field"):
            loop.decide(
                site=site,
                state={"k": "not_an_int"},
                fallback=lambda: "allow",
            )
