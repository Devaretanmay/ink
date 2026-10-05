"""Database migration and filesystem failure recovery tests for Ink."""

import sqlite3
import time

import pytest
from ink import DecisionSite, Ink
from ink.internal.decision_store import SCHEMA_VERSION, DecisionStore


def test_migration_from_v1_to_v4(tmp_path):
    path = tmp_path / "v1_legacy.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE sites(version TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "contract TEXT NOT NULL, created REAL NOT NULL)"
    )
    conn.execute(
        """CREATE TABLE decisions(id TEXT PRIMARY KEY, site TEXT NOT NULL,
        task TEXT NOT NULL, created REAL NOT NULL, state TEXT NOT NULL, choice TEXT NOT NULL,
        source TEXT NOT NULL, reason TEXT, artifact TEXT, prediction TEXT,
        confidence REAL, elapsed REAL NOT NULL, usage TEXT NOT NULL)"""
    )
    conn.execute(
        "CREATE TABLE outcomes(decision TEXT PRIMARY KEY, "
        "payload TEXT NOT NULL, created REAL NOT NULL)"
    )
    conn.execute(
        """CREATE TABLE artifacts(id TEXT PRIMARY KEY, site TEXT NOT NULL,
        payload TEXT NOT NULL, checksum TEXT NOT NULL, status TEXT NOT NULL,
        epoch REAL NOT NULL, profile TEXT, evidence TEXT)"""
    )
    conn.execute(
        """CREATE TABLE events(id INTEGER PRIMARY KEY, artifact TEXT NOT NULL,
        created REAL NOT NULL, previous TEXT NOT NULL, current TEXT NOT NULL,
        detail TEXT NOT NULL)"""
    )
    now = time.time()
    conn.execute("INSERT INTO sites VALUES (?,?,?,?)", ("s1", "site1", '{"text":"string"}', now))
    conn.execute(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "d1", "s1", "t1", now, '{"text":"hello"}', "allow", "fallback",
            "obs", None, None, 0.0, 0.01, "{}"
        ),
    )
    conn.execute("INSERT INTO outcomes VALUES (?,?,?)", ("d1", '{"quality":1.0}', now))
    conn.execute(
        "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
        ("a1", "s1", '{"engine_data":{}}', "chk1", "SHADOW", now, None, None),
    )
    conn.execute(
        "INSERT INTO events VALUES (?,?,?,?,?,?)",
        (1, "a1", now, "OBSERVE", "SHADOW", "{}")
    )
    conn.execute("PRAGMA user_version=1")
    conn.commit()
    conn.close()

    # Opening with DecisionStore triggers automatic migration 1 -> 2 -> 3 -> 4
    store = DecisionStore(path)
    try:
        assert store.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
        dec = store.rows("SELECT * FROM decisions")
        assert len(dec) == 1
        assert dec[0]["id"] == "d1"
        out = store.rows("SELECT * FROM outcomes")
        assert len(out) == 1
        assert out[0]["decision"] == "d1"
        cov = store.rows("SELECT * FROM state_coverage")
        assert len(cov) == 1
        assert cov[0]["observations"] == 1
    finally:
        store.close()


def test_unsupported_future_version_fails_loudly(tmp_path):
    path = tmp_path / "future.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE dummy(id INT)")
    conn.execute("PRAGMA user_version=99")
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="newer Ink"):
        DecisionStore(path)


def test_readonly_mode_inspects_cleanly(tmp_path):
    db_path = tmp_path / "ro.db"
    site = DecisionSite("ro.site", {"text": "string"}, ("ok", "fail"))
    with Ink(db_path) as client:
        client.decide(site=site, state={"text": "ping"}, fallback=lambda: "ok")

    with Ink(db_path, readonly=True) as ro_client:
        sites = ro_client.sites()
        assert len(sites) == 1
        assert sites[0]["name"] == "ro.site"


def test_default_database_migrates_atomically_from_legacy_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    site = DecisionSite("upgrade.site", {"text": "string"}, ("ok", "retry"))
    with Ink(".microloop/decisions.db") as legacy:
        legacy.decide(site=site, state={"text": "hello"}, fallback=lambda: "ok")

    with Ink() as upgraded:
        assert upgraded.sites()[0]["name"] == "upgrade.site"

    assert (tmp_path / ".ink/decisions.db").is_file()
    assert (tmp_path / ".microloop/decisions.db").is_file()
    with Ink(readonly=True) as reopened:
        assert reopened.sites()[0]["name"] == "upgrade.site"
