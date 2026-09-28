"""Task 2: local history, profiler, migrations, durability, data controls."""

import json
import sqlite3
import threading

import pytest
from microloop import DecisionSite, FallbackResult, Microloop
from microloop.internal.decision_store import SCHEMA_VERSION, DecisionStore, split_history

SITE = DecisionSite("task2.demo", {"refund": "boolean"}, ("refund", "specialist"))


def _v1_schema():
    return [
        """CREATE TABLE sites(version TEXT PRIMARY KEY, name TEXT NOT NULL,
           contract TEXT NOT NULL, created REAL NOT NULL)""",
        """CREATE TABLE decisions(id TEXT PRIMARY KEY, site TEXT NOT NULL REFERENCES sites(version),
           task TEXT NOT NULL, created REAL NOT NULL, state TEXT NOT NULL, choice TEXT NOT NULL,
           source TEXT NOT NULL, reason TEXT, artifact TEXT, prediction TEXT,
           confidence REAL, elapsed REAL NOT NULL, usage TEXT NOT NULL)""",
        """CREATE TABLE outcomes(decision TEXT PRIMARY KEY
           REFERENCES decisions(id) ON DELETE CASCADE,
           payload TEXT NOT NULL, created REAL NOT NULL)""",
        """CREATE TABLE artifacts(id TEXT PRIMARY KEY, site TEXT NOT NULL REFERENCES sites(version),
           payload TEXT NOT NULL, checksum TEXT NOT NULL, status TEXT NOT NULL,
           epoch REAL NOT NULL, profile TEXT, evidence TEXT)""",
        """CREATE UNIQUE INDEX one_candidate ON artifacts(site)
           WHERE status IN ('CANDIDATE','SHADOW','VERIFIED','ACTIVE')""",
        """CREATE TABLE events(id INTEGER PRIMARY KEY, artifact TEXT NOT NULL,
           created REAL NOT NULL, previous TEXT NOT NULL,
           current TEXT NOT NULL, detail TEXT NOT NULL)""",
        "CREATE INDEX decision_site_time ON decisions(site, created)",
    ]


def _make_v1_db(path):
    conn = sqlite3.connect(str(path))
    for stmt in _v1_schema():
        conn.execute(stmt)
    conn.execute("PRAGMA user_version=1")
    conn.commit()
    conn.close()


def test_migration_v1_to_v2_preserves_data(tmp_path):
    path = tmp_path / "old.db"
    _make_v1_db(path)
    # Seed one v1 row set directly.
    conn = sqlite3.connect(str(path))
    conn.execute("INSERT INTO sites VALUES (?,?,?,?)", ("v1", "task2.demo", '{"a":1}', 1.0))
    conn.execute(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "d1",
            "v1",
            "t1",
            1.0,
            "{}",
            "refund",
            "fallback",
            "observe",
            None,
            None,
            None,
            0.01,
            "{}",
        ),
    )
    conn.execute("INSERT INTO outcomes VALUES (?,?,?)", ("d1", '{"q":1}', 1.0))
    conn.execute(
        "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
        ("a1", "v1", "{}", "deadbeef", "SHADOW", 1.0, None, None),
    )
    conn.execute(
        "INSERT INTO events VALUES (?,?,?,?,?,?)", (1, "a1", 1.0, "CANDIDATE", "SHADOW", "{}")
    )
    conn.commit()
    conn.close()
    store = DecisionStore(path)
    try:
        assert store.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
        assert len(store.rows("SELECT * FROM decisions")) == 1
        assert len(store.rows("SELECT * FROM events")) == 1
        # v2 constraints enforced after migration.
        with pytest.raises(sqlite3.IntegrityError):
            with store.transaction() as db:
                db.execute(
                    "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "bad",
                        "v1",
                        "t",
                        1.0,
                        "{}",
                        "refund",
                        "nope",
                        None,
                        None,
                        None,
                        None,
                        0.01,
                        "{}",
                    ),
                )
    finally:
        store.close()


def test_profiler_counts_and_missing_vs_unsuccessful(tmp_path):
    with Microloop(tmp_path / "db") as client:
        for i in range(6):
            state = {"refund": i % 2 == 0}
            r = client.decide(
                site=SITE,
                state=state,
                task_id=f"t-{i % 3}",
                fallback=lambda state=state: FallbackResult(
                    "refund" if state["refund"] else "specialist", model_calls=1
                ),
            )
            if i < 4:
                quality = 1.0 if i < 3 else 0.0
                client.record_outcome(
                    r.decision_id,
                    quality=quality,
                    verifier="ledger",
                    verifier_version="1",
                    evidence={"i": i},
                )
        prof = client.profile(SITE)
        assert prof["observations"] == 6
        assert prof["distinct_tasks"] == 3
        assert prof["unique_states"] == 2
        assert prof["choices"] == {"refund": 3, "specialist": 3}
        assert prof["missing_outcomes"] == 2
        assert prof["successful_outcomes"] == 3
        assert prof["unsuccessful_outcomes"] == 1
        assert prof["usage"]["model_calls"] == 6
        assert prof["elapsed_p50"] is not None
        assert "profiler" in client.inspect(SITE)


def test_split_reproducible_after_restart_and_export(tmp_path):
    path = tmp_path / "db"
    with Microloop(path) as client:
        for i in range(30):
            state = {"refund": i % 2 == 0}
            r = client.decide(
                site=SITE,
                state=state,
                task_id=f"task-{i % 10}",
                fallback=lambda state=state: "refund" if state["refund"] else "specialist",
            )
            client.record_outcome(
                r.decision_id,
                quality=1.0,
                verifier="ledger",
                verifier_version="1",
                evidence={"i": i},
            )
        before = split_history(client.store.history(SITE.version))
        client.store.export(tmp_path / "exp.json")
    with Microloop(path) as client:
        after = split_history(client.store.history(SITE.version))
        assert [len(p) for p in before] == [len(p) for p in after]
        assert client.profile(SITE)["observations"] == 30
    data = json.loads((tmp_path / "exp.json").read_text())
    assert data["schema_version"] == SCHEMA_VERSION
    assert len(data["tables"]["decisions"]) == 30


def test_threaded_writers_and_restart_recovery(tmp_path):
    path = tmp_path / "db"
    with Microloop(path) as client:

        def write(n):
            for i in range(25):
                client.decide(
                    site=SITE,
                    state={"refund": bool(i % 2)},
                    task_id=f"th-{n}-{i}",
                    fallback=lambda: "refund",
                )

        threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert client.profile(SITE)["observations"] == 100
        client.store.checkpoint()
    # Restart recovers all rows; WAL mode persists.
    with Microloop(path) as client:
        assert client.profile(SITE)["observations"] == 100
        assert client.store.rows("PRAGMA journal_mode")[0]["journal_mode"].lower() == "wal"


def test_backup_retention_and_per_site_controls(tmp_path):
    path = tmp_path / "db"
    with Microloop(path) as client:
        r = client.decide(site=SITE, state={"refund": True}, fallback=lambda: "refund")
        client.record_outcome(
            r.decision_id,
            quality=1.0,
            verifier="ledger",
            verifier_version="1",
            evidence={"ok": True},
        )
        client.store.backup_to(tmp_path / "backup.db")
    backup = DecisionStore(tmp_path / "backup.db")
    try:
        assert len(backup.history(SITE.version)) == 1
    finally:
        backup.close()
    with Microloop(path) as client:
        assert client.store.retain_site(SITE.version, 1e20) in (0, 1)
        client.store.vacuum()


def test_migration_preserves_outcomes_and_all_foreign_keys(tmp_path):
    path = tmp_path / "old-evidence.db"
    _make_v1_db(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO sites VALUES (?,?,?,?)", ("s", "site", "{}", 1.0))
        db.execute(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "d",
                "s",
                "task",
                1.0,
                "{}",
                "refund",
                "fallback",
                "observe",
                None,
                None,
                None,
                0.01,
                "{}",
            ),
        )
        db.execute("INSERT INTO outcomes VALUES (?,?,?)", ("d", '{"quality":1}', 1.0))
    store = DecisionStore(path)
    try:
        assert len(store.rows("SELECT * FROM outcomes")) == 1
        assert store.rows("PRAGMA foreign_key_list(outcomes)")[0]["table"] == "decisions"
        assert not store.rows("PRAGMA foreign_key_check")
        with store.transaction() as db:
            db.execute("DELETE FROM decisions WHERE id='d'")
        assert store.rows("SELECT * FROM outcomes") == []
    finally:
        store.close()


def test_migration_rejects_corruption_atomically(tmp_path):
    path = tmp_path / "bad.db"
    _make_v1_db(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO sites VALUES (?,?,?,?)", ("s", "site", "{}", 1.0))
        db.execute(
            "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
            ("a", "s", "{}", "hash", "invalid", 1.0, None, None),
        )
    with pytest.raises(ValueError, match="Cannot migrate"):
        DecisionStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT status FROM artifacts").fetchone()[0] == "invalid"


def test_readonly_client_cannot_write(tmp_path):
    path = tmp_path / "db"
    with Microloop(path) as client:
        client.register(SITE)
    with Microloop(path, readonly=True) as client:
        assert client.inspect(SITE)["observations"] == 0
        with pytest.raises(sqlite3.OperationalError):
            client.register(SITE)


def test_repair_broken_v2_fk_demotes_existing_active_path(tmp_path):
    path = tmp_path / "broken-v2.db"
    # Simulate the schema produced by the original migration, including lost outcomes.
    _make_v1_db(path)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE outcomes")
        db.execute(
            "CREATE TABLE outcomes(decision TEXT PRIMARY KEY "
            "REFERENCES decisions_legacy_v1(id) ON DELETE CASCADE, "
            "payload TEXT NOT NULL, created REAL NOT NULL)"
        )
        db.execute("INSERT INTO sites VALUES (?,?,?,?)", ("s", "site", "{}", 1.0))
        db.execute(
            "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
            ("a", "s", "{}", "hash", "ACTIVE", 1.0, None, None),
        )
        db.execute("PRAGMA user_version=2")
    store = DecisionStore(path)
    try:
        assert store.rows("PRAGMA foreign_key_list(outcomes)")[0]["table"] == "decisions"
        assert store.rows("SELECT status FROM artifacts")[0]["status"] == "SHADOW"
        assert (
            "legacy_migration_lost_outcomes" in store.rows("SELECT detail FROM events")[0]["detail"]
        )
    finally:
        store.close()
