"""Transactional local decision history. No network or telemetry."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .contracts import canonical

SCHEMA_VERSION = 3

SCHEMA = [
    """CREATE TABLE sites(version TEXT PRIMARY KEY, name TEXT NOT NULL,
       contract TEXT NOT NULL, created REAL NOT NULL)""",
    """CREATE TABLE decisions(id TEXT PRIMARY KEY,
       site TEXT NOT NULL REFERENCES sites(version),
       task TEXT NOT NULL, created REAL NOT NULL, state TEXT NOT NULL, choice TEXT NOT NULL,
       source TEXT NOT NULL CHECK(source IN ('fallback','fast_path')),
       reason TEXT, artifact TEXT, prediction TEXT,
       confidence REAL, elapsed REAL NOT NULL, usage TEXT NOT NULL)""",
    """CREATE TABLE outcomes(decision TEXT PRIMARY KEY REFERENCES decisions(id) ON DELETE CASCADE,
       payload TEXT NOT NULL, created REAL NOT NULL)""",
    """CREATE TABLE artifacts(id TEXT PRIMARY KEY, site TEXT NOT NULL REFERENCES sites(version),
       payload TEXT NOT NULL, checksum TEXT NOT NULL,
       status TEXT NOT NULL CHECK(status IN ('CANDIDATE','SHADOW','VERIFIED','ACTIVE','RETIRED')),
       epoch REAL NOT NULL, profile TEXT, evidence TEXT)""",
    """CREATE UNIQUE INDEX one_candidate ON artifacts(site)
       WHERE status IN ('CANDIDATE','SHADOW','VERIFIED','ACTIVE')""",
    """CREATE TABLE events(id INTEGER PRIMARY KEY,
       artifact TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
       created REAL NOT NULL, previous TEXT NOT NULL,
       current TEXT NOT NULL, detail TEXT NOT NULL)""",
    "CREATE INDEX decision_site_time ON decisions(site, created)",
]


def _migrate_1_to_2(db):
    """Harden v1 tables with CHECK constraints and event FK.

    Rebuilds tables carrying new constraints; validates status/source values first.
    """
    bad_status = db.execute(
        "SELECT DISTINCT status FROM artifacts "
        "WHERE status NOT IN ('CANDIDATE','SHADOW','VERIFIED','ACTIVE','RETIRED')"
    ).fetchall()
    if bad_status:
        raise ValueError(f"Cannot migrate artifacts with status: {[r[0] for r in bad_status]}")
    bad_source = db.execute(
        "SELECT DISTINCT source FROM decisions WHERE source NOT IN ('fallback','fast_path')"
    ).fetchall()
    if bad_source:
        raise ValueError(f"Cannot migrate decisions with source: {[r[0] for r in bad_source]}")
    orphan_events = db.execute(
        "SELECT COUNT(*) FROM events WHERE artifact NOT IN (SELECT id FROM artifacts)"
    ).fetchone()[0]
    if orphan_events:
        raise ValueError(f"Cannot migrate {orphan_events} orphan lifecycle events")
    # Renaming a referenced parent rewrites the child's FK; dropping it would
    # cascade-delete outcomes. Preserve/rebuild the child within this transaction.
    db.execute("CREATE TEMP TABLE saved_outcomes AS SELECT * FROM outcomes")
    db.execute("DROP TABLE outcomes")
    db.execute("DROP INDEX IF EXISTS one_candidate")
    db.execute("DROP INDEX IF EXISTS decision_site_time")
    for table, definition in (
        ("decisions", [s for s in SCHEMA if s.startswith("CREATE TABLE decisions")][0]),
        ("artifacts", [s for s in SCHEMA if s.startswith("CREATE TABLE artifacts")][0]),
        ("events", [s for s in SCHEMA if s.startswith("CREATE TABLE events")][0]),
    ):
        columns = [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
        names = ",".join(columns)
        db.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy_v1")
        db.execute(definition)
        db.execute(f"INSERT INTO {table}({names}) SELECT {names} FROM {table}_legacy_v1")
        db.execute(f"DROP TABLE {table}_legacy_v1")
    db.execute([s for s in SCHEMA if "one_candidate" in s][0])
    db.execute([s for s in SCHEMA if "decision_site_time" in s][0])
    db.execute(next(s for s in SCHEMA if s.startswith("CREATE TABLE outcomes")))
    db.execute("INSERT INTO outcomes SELECT * FROM saved_outcomes")
    db.execute("DROP TABLE saved_outcomes")


def _migrate_2_to_3(db):
    """Repair the outcome FK from the original v1→v2 migration, if affected.

    Deleted evidence cannot be reconstructed. Demote any affected active paths;
    new factual evidence must be collected before qualification can succeed.
    """
    targets = {r[2] for r in db.execute("PRAGMA foreign_key_list(outcomes)")}
    if targets == {"decisions"}:
        return
    db.execute("CREATE TEMP TABLE saved_outcomes AS SELECT * FROM outcomes")
    db.execute("DROP TABLE outcomes")
    db.execute(next(s for s in SCHEMA if s.startswith("CREATE TABLE outcomes")))
    db.execute("INSERT INTO outcomes SELECT * FROM saved_outcomes")
    db.execute("DROP TABLE saved_outcomes")
    now = time.time()
    for row in db.execute("SELECT id FROM artifacts WHERE status='ACTIVE'").fetchall():
        db.execute("UPDATE artifacts SET status='SHADOW',epoch=? WHERE id=?", (now, row[0]))
        db.execute(
            "INSERT INTO events(artifact,created,previous,current,detail) VALUES (?,?,?,?,?)",
            (
                row[0],
                now,
                "ACTIVE",
                "SHADOW",
                canonical({"reason": "legacy_migration_lost_outcomes"}),
            ),
        )


MIGRATIONS = {1: _migrate_1_to_2, 2: _migrate_2_to_3}


class DecisionStore:
    def __init__(self, path=".microloop/decisions.db", *, readonly=False):
        self.path = str(path)
        self.readonly = readonly
        if self.path != ":memory:" and not readonly:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        target = Path(path).resolve().as_uri() + "?mode=ro" if readonly else self.path
        self.conn = sqlite3.connect(target, uri=readonly, timeout=10, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        if readonly:
            if self.conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                self.conn.close()
                raise ValueError("Decision database requires migration")
            return
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError("Decision database was created by a newer Microloop")
            if version == 0:
                for statement in SCHEMA:
                    db.execute(statement)
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            else:
                while version < SCHEMA_VERSION:
                    migrate = MIGRATIONS.get(version)
                    if migrate is None:
                        raise ValueError(f"No migration from decision schema {version}")
                    migrate(db)
                    version += 1
                    db.execute(f"PRAGMA user_version={version}")

    @contextmanager
    def transaction(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise

    def rows(self, sql, parameters=()):
        with self.lock:
            return [dict(row) for row in self.conn.execute(sql, parameters)]

    def history(self, site):
        rows = self.rows(
            """SELECT d.*, o.payload AS outcome FROM decisions d
            LEFT JOIN outcomes o ON o.decision=d.id WHERE d.site=? ORDER BY d.created,d.id""",
            (site,),
        )
        for row in rows:
            for key in ("state", "usage", "prediction", "outcome"):
                row[key] = json.loads(row[key]) if row[key] else None
        return rows

    def export(self, path):
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                data = {
                    table: self.rows(f"SELECT * FROM {table}")
                    for table in ("sites", "decisions", "outcomes", "artifacts", "events")
                }
            finally:
                self.conn.rollback()
        Path(path).write_text(canonical({"schema_version": SCHEMA_VERSION, "tables": data}) + "\n")

    def backup_to(self, dest):
        """Consistent SQLite backup; dest path must not be the live database."""
        if self.path != ":memory:":
            try:
                if Path(dest).expanduser().resolve() == Path(self.path).expanduser().resolve():
                    raise ValueError("Backup destination must not be the live database")
            except OSError:
                pass
        with self.lock:
            target = sqlite3.connect(str(dest))
            try:
                self.conn.backup(target)
            finally:
                target.close()

    def checkpoint(self):
        with self.lock:
            return self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()

    def vacuum(self):
        with self.lock:
            self.conn.execute("VACUUM")

    def retain_since(self, timestamp):
        # Freeze evidence for all existing artifacts. Deleting source rows would invalidate audits.
        with self.transaction() as db:
            return db.execute(
                """DELETE FROM decisions WHERE created < ? AND site NOT IN
                (SELECT site FROM artifacts)""",
                (timestamp,),
            ).rowcount

    def retain_site(self, site_version, timestamp):
        """Per-site retention; refuses to delete evidence behind existing artifacts."""
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM artifacts WHERE site=?", (site_version,)).fetchone():
                return 0
            return db.execute(
                "DELETE FROM decisions WHERE site=? AND created < ?",
                (site_version, timestamp),
            ).rowcount

    def close(self):
        with self.lock:
            self.conn.close()


def split_history(rows):
    """Chronological task groups; outcomes never leak across the three partitions."""
    # Purge entire task groups spanning temporal boundaries, rather than moving future
    # observations into training because their task first appeared earlier.
    a, b = int(len(rows) * 0.6), int(len(rows) * 0.8)
    sections = (rows[:a], rows[a:b], rows[b:])
    memberships = {}
    for index, part in enumerate(sections):
        for row in part:
            memberships.setdefault(row["task"], set()).add(index)
    return [[row for row in part if len(memberships[row["task"]]) == 1] for part in sections]
