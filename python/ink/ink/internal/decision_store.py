"""Transactional local decision history. No network or telemetry."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .contracts import InkArtifact, canonical

SCHEMA_VERSION = 6

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
    """CREATE UNIQUE INDEX one_candidate_per_engine
       ON artifacts(site, json_extract(payload, '$.engine_data.engine'))
       WHERE status IN ('CANDIDATE','SHADOW','VERIFIED','ACTIVE')""",
    """CREATE TABLE events(id INTEGER PRIMARY KEY,
       artifact TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
       created REAL NOT NULL, previous TEXT NOT NULL,
       current TEXT NOT NULL, detail TEXT NOT NULL)""",
    "CREATE INDEX decision_site_time ON decisions(site, created)",
    """CREATE TABLE state_coverage(site TEXT NOT NULL, state TEXT NOT NULL,
       observations INTEGER NOT NULL DEFAULT 0, fast_served INTEGER NOT NULL DEFAULT 0,
       outcomes INTEGER NOT NULL DEFAULT 0, quality_sum REAL NOT NULL DEFAULT 0.0,
       PRIMARY KEY(site, state))""",
    """CREATE TABLE promotion_records(artifact TEXT PRIMARY KEY
       REFERENCES artifacts(id) ON DELETE CASCADE,
       decided REAL NOT NULL, qualified INTEGER NOT NULL,
       holdout_samples INTEGER NOT NULL, shadow_samples INTEGER NOT NULL,
       quality_lower REAL, delta_lower REAL, agreement REAL)""",
    """CREATE TABLE drift_checks(id INTEGER PRIMARY KEY,
       artifact TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
       created REAL NOT NULL, demoted INTEGER NOT NULL,
       active_samples INTEGER NOT NULL, comparison_samples INTEGER NOT NULL,
       missing_outcomes INTEGER NOT NULL, quality_lower REAL, delta_lower REAL,
       uncovered_rate REAL)""",
    """CREATE TABLE artifact_links(parent TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
       child TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
       created REAL NOT NULL, PRIMARY KEY(parent, child))""",
    """CREATE TABLE evidence_epochs(epoch_id TEXT PRIMARY KEY,
       app_id TEXT NOT NULL DEFAULT 'default',
       site TEXT NOT NULL, site_version TEXT NOT NULL,
       artifact_id TEXT NOT NULL, representation_version TEXT NOT NULL,
       engine TEXT NOT NULL, verifier TEXT NOT NULL,
       verifier_version TEXT NOT NULL, schema_hash TEXT NOT NULL,
       choices_hash TEXT NOT NULL, requirements TEXT NOT NULL,
       status TEXT NOT NULL CHECK(status IN ('OPEN','QUALIFIED','REVOKED','SUPERSEDED')),
       created REAL NOT NULL, last_updated REAL NOT NULL)""",
    """CREATE TABLE progressive_evidence(id INTEGER PRIMARY KEY AUTOINCREMENT,
       epoch_id TEXT NOT NULL REFERENCES evidence_epochs(epoch_id) ON DELETE CASCADE,
       region_id TEXT NOT NULL,
       decision_id TEXT NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
       state_canonical TEXT NOT NULL, choice TEXT NOT NULL,
       expected_choice TEXT, quality REAL NOT NULL,
       verified_match INTEGER NOT NULL, distance REAL, created REAL NOT NULL)""",
    """CREATE TABLE region_lifecycle(region_id TEXT NOT NULL,
       artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
       site TEXT NOT NULL,
       status TEXT NOT NULL CHECK(status IN ('CANDIDATE','SHADOW','QUALIFIED','ACTIVE','REVOKED','DEGRADED')),
       sample_count INTEGER NOT NULL DEFAULT 0,
       verified_positive INTEGER NOT NULL DEFAULT 0,
       verified_negative INTEGER NOT NULL DEFAULT 0,
       quality_lower REAL NOT NULL DEFAULT 0.0,
       radius REAL NOT NULL, negative_margin REAL NOT NULL,
       last_evaluated REAL NOT NULL,
       PRIMARY KEY(artifact_id, region_id))""",
    "CREATE INDEX progressive_ev_epoch_reg ON progressive_evidence(epoch_id, region_id)",
    "CREATE INDEX evidence_epoch_app_site ON evidence_epochs(app_id, site, status)",
    """CREATE TABLE policy_observations(decision TEXT PRIMARY KEY
       REFERENCES decisions(id) ON DELETE CASCADE,
       site TEXT NOT NULL, state TEXT NOT NULL, host_choice TEXT NOT NULL,
       proposed_choice TEXT NOT NULL, scores TEXT NOT NULL,
       representation TEXT, confidence REAL NOT NULL, ambiguity REAL,
       act_probability REAL, checkpoint_identity TEXT NOT NULL,
       verified_outcome TEXT, created REAL NOT NULL, verified_at REAL)""",
    "CREATE INDEX policy_observation_site_time ON policy_observations(site, created)",
    """CREATE TABLE authority_events(id INTEGER PRIMARY KEY AUTOINCREMENT,
       step INTEGER NOT NULL, site TEXT NOT NULL, engine TEXT NOT NULL,
       artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
       previous_status TEXT NOT NULL, new_status TEXT NOT NULL,
       support_n INTEGER NOT NULL, quality REAL, lower_bound REAL,
       coverage REAL, reason TEXT NOT NULL, created REAL NOT NULL)""",
    "CREATE INDEX authority_event_site_step ON authority_events(site, step)",
    """CREATE TABLE non_promotion_events(id INTEGER PRIMARY KEY AUTOINCREMENT,
       step INTEGER NOT NULL, site TEXT NOT NULL, engine TEXT NOT NULL,
       artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
       reason TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS policy_utility_evidence(
       site_key TEXT PRIMARY KEY,
       site_version TEXT NOT NULL,
       checkpoint_revision TEXT NOT NULL,
       state TEXT NOT NULL,
       evidence_json TEXT NOT NULL,
       updated_at REAL NOT NULL)""",
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


def _migrate_3_to_4(db):
    """Add queryable coverage, promotion, drift, and lineage tables with backfill."""
    for statement in [
        s
        for s in SCHEMA
        if s.startswith("CREATE TABLE state_coverage")
        or s.startswith("CREATE TABLE promotion_records")
        or s.startswith("CREATE TABLE drift_checks")
        or s.startswith("CREATE TABLE artifact_links")
    ]:
        db.execute(statement)
    db.execute(
        """INSERT INTO state_coverage(site, state, observations, fast_served, outcomes, quality_sum)
        SELECT d.site, d.state, COUNT(*),
          SUM(CASE WHEN d.source='fast_path' THEN 1 ELSE 0 END),
          SUM(CASE WHEN o.decision IS NULL THEN 0 ELSE 1 END),
          COALESCE(SUM(CAST(json_extract(o.payload, '$.quality') AS REAL)), 0.0)
        FROM decisions d LEFT JOIN outcomes o ON o.decision=d.id
        GROUP BY d.site, d.state"""
    )
    for row in db.execute("SELECT id, evidence FROM artifacts WHERE evidence IS NOT NULL"):
        try:
            evidence = json.loads(row["evidence"])
        except ValueError:
            continue
        holdout, shadow = evidence.get("holdout") or {}, evidence.get("shadow") or {}
        db.execute(
            "INSERT OR IGNORE INTO promotion_records VALUES (?,?,?,?,?,?,?,?)",
            (
                row["id"],
                time.time(),
                int(bool(evidence.get("qualified"))),
                holdout.get("samples", 0),
                shadow.get("samples", 0),
                holdout.get("quality_lower"),
                shadow.get("delta_lower"),
                holdout.get("agreement"),
            ),
        )
    for row in db.execute("SELECT artifact, created, previous, current, detail FROM events"):
        try:
            detail = json.loads(row["detail"])
        except ValueError:
            detail = {}
        if row["previous"] == "ACTIVE" and row["current"] == "SHADOW":
            db.execute(
                """INSERT INTO drift_checks(artifact, created, demoted, active_samples,
                comparison_samples, missing_outcomes, quality_lower, delta_lower, uncovered_rate)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    row["artifact"],
                    row["created"],
                    1,
                    detail.get("active_samples", 0),
                    detail.get("comparison_samples", 0),
                    detail.get("missing_outcomes", 0),
                    detail.get("quality_lower"),
                    detail.get("delta_lower"),
                    detail.get("uncovered_rate"),
                ),
            )
        if row["current"] == "RETIRED" and isinstance(detail, dict) and detail.get("replaced_by"):
            db.execute(
                "INSERT OR IGNORE INTO artifact_links VALUES (?,?,?)",
                (row["artifact"], detail["replaced_by"], row["created"]),
            )


MIGRATIONS[3] = _migrate_3_to_4


def _migrate_4_to_5(db):
    """Add progressive evidence epochs and region lifecycle tables."""
    for statement in [
        s
        for s in SCHEMA
        if s.startswith("CREATE TABLE evidence_epochs")
        or s.startswith("CREATE TABLE progressive_evidence")
        or s.startswith("CREATE TABLE region_lifecycle")
        or s.startswith("CREATE INDEX progressive_ev_epoch_reg")
        or s.startswith("CREATE INDEX evidence_epoch_app_site")
    ]:
        stmt = statement.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ").replace(
            "CREATE INDEX ", "CREATE INDEX IF NOT EXISTS "
        )
        db.execute(stmt)


MIGRATIONS[4] = _migrate_4_to_5


def _migrate_5_to_6(db):
    """Permit one live serving artifact per engine and add control-plane ledgers."""
    db.execute("DROP INDEX IF EXISTS one_candidate")
    db.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS one_candidate_per_engine
        ON artifacts(site, json_extract(payload, '$.engine_data.engine'))
        WHERE status IN ('CANDIDATE','SHADOW','VERIFIED','ACTIVE')"""
    )
    for statement in SCHEMA:
        if (
            statement.startswith("CREATE TABLE policy_observations")
            or statement.startswith("CREATE INDEX policy_observation_site_time")
            or statement.startswith("CREATE TABLE authority_events")
            or statement.startswith("CREATE INDEX authority_event_site_step")
            or statement.startswith("CREATE TABLE non_promotion_events")
        ):
            stmt = statement.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ").replace(
                "CREATE INDEX ", "CREATE INDEX IF NOT EXISTS "
            )
            db.execute(stmt)


MIGRATIONS[5] = _migrate_5_to_6


class DecisionStore:
    def __init__(self, path=".ink/decisions.db", *, readonly=False, timeout=1.0):
        self.path = str(path)
        self.readonly = readonly
        self.timeout = float(timeout)
        if self.path != ":memory:" and not readonly:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        target = Path(path).resolve().as_uri() + "?mode=ro" if readonly else self.path
        self.conn = sqlite3.connect(
            target, uri=readonly, timeout=self.timeout, check_same_thread=False
        )
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
                raise ValueError("Decision database was created by a newer Ink")
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
            db.execute(
                """CREATE TABLE IF NOT EXISTS policy_utility_evidence(
                   site_key TEXT PRIMARY KEY,
                   site_version TEXT NOT NULL,
                   checkpoint_revision TEXT NOT NULL,
                   state TEXT NOT NULL,
                   evidence_json TEXT NOT NULL,
                   updated_at REAL NOT NULL)"""
            )

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
        self._check_destination(path)
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                data = {
                    table: self.rows(f"SELECT * FROM {table}")
                    for table in (
                        "sites",
                        "decisions",
                        "outcomes",
                        "artifacts",
                        "events",
                        "state_coverage",
                        "promotion_records",
                        "drift_checks",
                        "artifact_links",
                    )
                }
            finally:
                self.conn.rollback()
        Path(path).write_text(canonical({"schema_version": SCHEMA_VERSION, "tables": data}) + "\n")

    def backup_to(self, dest):
        """Consistent SQLite backup; dest path must not be the live database."""
        self._check_destination(dest)
        with self.lock:
            target = sqlite3.connect(str(dest))
            try:
                self.conn.backup(target)
            finally:
                target.close()

    def _check_destination(self, dest):
        if self.path == ":memory:":
            return
        target, source = Path(dest).expanduser().resolve(), Path(self.path).resolve()
        protected = [source, Path(str(source) + "-wal"), Path(str(source) + "-shm")]
        if target in protected or any(
            item.exists() and target.exists() and item.samefile(target) for item in protected
        ):
            raise ValueError("Destination must not overwrite the live database or journal")

    def checkpoint(self):
        with self.lock:
            return self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()

    def vacuum(self):
        with self.lock:
            self.conn.execute("VACUUM")

    def _rebuild_coverage(self, db, site_version):
        db.execute("DELETE FROM state_coverage WHERE site=?", (site_version,))
        db.execute(
            """INSERT INTO state_coverage(site, state, observations, fast_served, outcomes,
            quality_sum) SELECT d.site, d.state, COUNT(*),
              SUM(CASE WHEN d.source='fast_path' THEN 1 ELSE 0 END),
              SUM(CASE WHEN o.decision IS NULL THEN 0 ELSE 1 END),
              COALESCE(SUM(CAST(json_extract(o.payload, '$.quality') AS REAL)), 0.0)
            FROM decisions d LEFT JOIN outcomes o ON o.decision=d.id
            WHERE d.site=? GROUP BY d.site, d.state""",
            (site_version,),
        )

    def retain_since(self, timestamp):
        # Freeze evidence for all existing artifacts. Deleting source rows would invalidate audits.
        with self.transaction() as db:
            count = db.execute(
                """DELETE FROM decisions WHERE created < ? AND site NOT IN
                (SELECT site FROM artifacts)""",
                (timestamp,),
            ).rowcount
            for row in db.execute(
                "SELECT DISTINCT site FROM decisions WHERE site NOT IN (SELECT site FROM artifacts)"
            ):
                self._rebuild_coverage(db, row[0])
            return count

    def retain_site(self, site_version, timestamp):
        """Per-site retention; refuses to delete evidence behind existing artifacts."""
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM artifacts WHERE site=?", (site_version,)).fetchone():
                return 0
            count = db.execute(
                "DELETE FROM decisions WHERE site=? AND created < ?",
                (site_version, timestamp),
            ).rowcount
            self._rebuild_coverage(db, site_version)
            return count

    def open_or_get_epoch(
        self,
        app_id: str,
        site: str,
        site_version: str,
        artifact_id: str,
        representation_version: str,
        engine: str,
        verifier: str,
        verifier_version: str,
        schema_hash: str,
        choices_hash: str,
        requirements: dict | str,
    ) -> str:
        req_str = canonical(requirements) if isinstance(requirements, dict) else str(requirements)
        now = time.time()
        with self.transaction() as db:
            row = db.execute(
                """SELECT epoch_id, site_version, artifact_id, representation_version,
                          engine, verifier, verifier_version, schema_hash, choices_hash, requirements
                   FROM evidence_epochs
                   WHERE app_id=? AND site=? AND status='OPEN'
                   ORDER BY created DESC LIMIT 1""",
                (app_id, site),
            ).fetchone()
            if row:
                compatible = (
                    row["site_version"] == site_version
                    and row["artifact_id"] == artifact_id
                    and row["representation_version"] == representation_version
                    and row["engine"] == engine
                    and row["verifier"] == verifier
                    and row["verifier_version"] == verifier_version
                    and row["schema_hash"] == schema_hash
                    and row["choices_hash"] == choices_hash
                    and row["requirements"] == req_str
                )
                if compatible:
                    db.execute(
                        "UPDATE evidence_epochs SET last_updated=? WHERE epoch_id=?",
                        (now, row["epoch_id"]),
                    )
                    return row["epoch_id"]
                else:
                    db.execute(
                        "UPDATE evidence_epochs SET status='SUPERSEDED', last_updated=? WHERE epoch_id=?",
                        (now, row["epoch_id"]),
                    )

            import uuid
            epoch_id = f"ep-{uuid.uuid4().hex[:12]}"
            db.execute(
                """INSERT INTO evidence_epochs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    epoch_id,
                    app_id,
                    site,
                    site_version,
                    artifact_id,
                    representation_version,
                    engine,
                    verifier,
                    verifier_version,
                    schema_hash,
                    choices_hash,
                    req_str,
                    "OPEN",
                    now,
                    now,
                ),
            )
            return epoch_id

    def record_progressive_evidence(
        self,
        epoch_id: str,
        region_id: str,
        decision_id: str,
        state_canonical: str,
        choice: str,
        expected_choice: str | None,
        quality: float,
        verified_match: int,
        distance: float | None = None,
    ):
        now = time.time()
        with self.transaction() as db:
            db.execute(
                """INSERT INTO progressive_evidence(epoch_id, region_id, decision_id,
                   state_canonical, choice, expected_choice, quality, verified_match, distance, created)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    epoch_id,
                    region_id,
                    decision_id,
                    state_canonical,
                    choice,
                    expected_choice,
                    quality,
                    verified_match,
                    distance,
                    now,
                ),
            )
            db.execute(
                "UPDATE evidence_epochs SET last_updated=? WHERE epoch_id=?",
                (now, epoch_id),
            )

    def get_progressive_evidence(self, epoch_id: str, region_id: str | None = None) -> list[dict]:
        if region_id:
            return self.rows(
                "SELECT * FROM progressive_evidence WHERE epoch_id=? AND region_id=? ORDER BY created ASC",
                (epoch_id, region_id),
            )
        return self.rows(
            "SELECT * FROM progressive_evidence WHERE epoch_id=? ORDER BY created ASC",
            (epoch_id,),
        )

    def sync_region_lifecycle(
        self,
        artifact_id: str,
        site: str,
        region_id: str,
        status: str,
        radius: float,
        negative_margin: float,
        sample_count: int = 0,
        verified_positive: int = 0,
        verified_negative: int = 0,
        quality_lower: float = 0.0,
    ):
        now = time.time()
        with self.transaction() as db:
            db.execute(
                """INSERT INTO region_lifecycle(region_id, artifact_id, site, status,
                   sample_count, verified_positive, verified_negative, quality_lower,
                   radius, negative_margin, last_evaluated)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(artifact_id, region_id) DO UPDATE SET
                   status=excluded.status,
                   sample_count=excluded.sample_count,
                   verified_positive=excluded.verified_positive,
                   verified_negative=excluded.verified_negative,
                   quality_lower=excluded.quality_lower,
                   radius=excluded.radius,
                   negative_margin=excluded.negative_margin,
                   last_evaluated=excluded.last_evaluated""",
                (
                    region_id,
                    artifact_id,
                    site,
                    status,
                    sample_count,
                    verified_positive,
                    verified_negative,
                    quality_lower,
                    radius,
                    negative_margin,
                    now,
                ),
            )

    def get_active_regions(self, artifact_id: str) -> set[str]:
        rows = self.rows(
            "SELECT region_id FROM region_lifecycle WHERE artifact_id=? AND status='ACTIVE'",
            (artifact_id,),
        )
        return {r["region_id"] for r in rows}

    def get_region_lifecycles(self, artifact_id: str) -> list[dict]:
        return self.rows(
            "SELECT * FROM region_lifecycle WHERE artifact_id=? ORDER BY region_id ASC",
            (artifact_id,),
        )

    def revoke_epoch_and_regions(self, artifact_id: str, db=None):
        now = time.time()
        if db is not None:
            db.execute(
                "UPDATE evidence_epochs SET status='REVOKED', last_updated=? WHERE artifact_id=? AND status='OPEN'",
                (now, artifact_id),
            )
            db.execute(
                "UPDATE region_lifecycle SET status='REVOKED', last_evaluated=? WHERE artifact_id=?",
                (now, artifact_id),
            )
        else:
            with self.transaction() as conn:
                conn.execute(
                    "UPDATE evidence_epochs SET status='REVOKED', last_updated=? WHERE artifact_id=? AND status='OPEN'",
                    (now, artifact_id),
                )
                conn.execute(
                    "UPDATE region_lifecycle SET status='REVOKED', last_evaluated=? WHERE artifact_id=?",
                    (now, artifact_id),
                )

    def get_site(self, version: str) -> dict | None:
        rows = self.rows("SELECT * FROM sites WHERE version=?", (version,))
        return rows[0] if rows else None

    def get_site_by_name(self, name: str) -> dict | None:
        rows = self.rows("SELECT * FROM sites WHERE name=? ORDER BY created DESC LIMIT 1", (name,))
        return rows[0] if rows else None

    def get_site_contract(self, name: str) -> str | None:
        rows = self.rows("SELECT contract FROM sites WHERE name=? ORDER BY created DESC LIMIT 1", (name,))
        return rows[0]["contract"] if rows else None

    def get_all_sites(self) -> list[dict]:
        return self.rows("SELECT * FROM sites ORDER BY name, created")

    def save_site(self, version: str, name: str, contract_json: str, created: float):
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO sites VALUES (?,?,?,?)", (version, name, contract_json, created))

    def get_artifact(self, artifact_id: str) -> dict | None:
        rows = self.rows("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
        return rows[0] if rows else None

    def get_artifact_for_site(self, site_version: str, status: str | None = None) -> dict | None:
        if status:
            rows = self.rows("SELECT * FROM artifacts WHERE site=? AND status=?", (site_version, status))
        else:
            rows = self.rows(
                "SELECT * FROM artifacts WHERE site=? AND status!='RETIRED' ORDER BY epoch DESC LIMIT 1",
                (site_version,),
            )
        return rows[0] if rows else None

    def get_artifacts_for_site(
        self, site_version: str, status: str | None = None
    ) -> list[dict]:
        """Return live artifacts in serving order: Exact, then Linear.

        Policy-model artifacts are deliberately excluded from this production
        dispatcher view; they are control-plane inputs, not serving engines.
        """
        if status:
            return self.rows(
                "SELECT * FROM artifacts WHERE site=? AND status=? ORDER BY epoch DESC",
                (site_version, status),
            )
        return self.rows(
            "SELECT * FROM artifacts WHERE site=? AND status!='RETIRED' ORDER BY epoch DESC",
            (site_version,),
        )

    def get_active_artifact(self, site_version: str) -> dict | None:
        rows = self.rows(
            "SELECT * FROM artifacts WHERE site=? AND status='ACTIVE' ORDER BY epoch DESC LIMIT 1",
            (site_version,),
        )
        return rows[0] if rows else None

    def get_artifact_view(self, artifact_id: str) -> InkArtifact | None:
        row = self.get_artifact(artifact_id)
        return InkArtifact.from_row(row) if row else None

    def get_active_artifact_view(self, site_version: str) -> InkArtifact | None:
        row = self.get_active_artifact(site_version)
        return InkArtifact.from_row(row) if row else None

    def save_artifact(
        self,
        artifact_id: str,
        site_version: str,
        payload_json: str,
        checksum: str,
        status: str,
        epoch: float,
        profile_json: str | None = None,
        evidence_json: str | None = None,
        replace_existing: bool = False,
    ):
        with self.transaction() as db:
            engine_key = json.loads(payload_json).get("engine_data", {}).get("engine")
            previous = db.execute(
                """SELECT id, status FROM artifacts WHERE site=? AND status!='RETIRED'
                AND json_extract(payload, '$.engine_data.engine')=?""",
                (site_version, engine_key),
            ).fetchone()
            if previous:
                if not replace_existing:
                    raise ValueError("Site already has a candidate; explicitly replace to recompile")
                db.execute("UPDATE artifacts SET status='RETIRED' WHERE id=?", (previous["id"],))
                self.record_event(
                    previous["id"],
                    previous["status"],
                    "RETIRED",
                    json.dumps({"replaced_by": artifact_id}),
                    db=db,
                )
            db.execute(
                "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
                (
                    artifact_id,
                    site_version,
                    payload_json,
                    checksum,
                    status,
                    epoch,
                    profile_json,
                    evidence_json,
                ),
            )
            self.record_event(artifact_id, "NONE", status, json.dumps({"reason": "compiled"}), db=db)

    def retire_artifact(self, artifact_id: str, replaced_by: str | None = None, db=None):
        detail = json.dumps({"replaced_by": replaced_by}) if replaced_by else "{}"
        if db is not None:
            db.execute("UPDATE artifacts SET status='RETIRED' WHERE id=?", (artifact_id,))
            self.record_event(artifact_id, "ACTIVE", "RETIRED", detail, db=db)
        else:
            with self.transaction() as conn:
                conn.execute("UPDATE artifacts SET status='RETIRED' WHERE id=?", (artifact_id,))
                self.record_event(artifact_id, "ACTIVE", "RETIRED", detail, db=conn)

    def demote_artifact(
        self,
        artifact_id: str,
        new_status: str = "SHADOW",
        epoch: float | None = None,
        evidence: str | None = None,
        db=None,
    ) -> int:
        ep = epoch if epoch is not None else time.time()
        if db is not None:
            count = db.execute(
                "UPDATE artifacts SET status=?, epoch=? WHERE id=? AND status='ACTIVE'",
                (new_status, ep, artifact_id),
            ).rowcount
            if count:
                self.revoke_epoch_and_regions(artifact_id, db=db)
                self.record_event(artifact_id, "ACTIVE", new_status, evidence or "{}", db=db)
            return count
        else:
            with self.transaction() as conn:
                count = conn.execute(
                    "UPDATE artifacts SET status=?, epoch=? WHERE id=? AND status='ACTIVE'",
                    (new_status, ep, artifact_id),
                ).rowcount
                if count:
                    self.revoke_epoch_and_regions(artifact_id, db=conn)
                    self.record_event(artifact_id, "ACTIVE", new_status, evidence or "{}", db=conn)
                return count

    def promote_artifact(
        self,
        artifact_id: str,
        new_status: str = "ACTIVE",
        epoch: float | None = None,
        evidence: str | None = None,
        db=None,
    ):
        ep = epoch if epoch is not None else time.time()
        ev = evidence or "{}"
        if db is not None:
            db.execute(
                "UPDATE artifacts SET status=?, epoch=?, evidence=? WHERE id=?",
                (new_status, ep, ev, artifact_id),
            )
            self.record_event(artifact_id, "SHADOW", new_status, ev, db=db)
        else:
            with self.transaction() as conn:
                conn.execute(
                    "UPDATE artifacts SET status=?, epoch=?, evidence=? WHERE id=?",
                    (new_status, ep, ev, artifact_id),
                )
                self.record_event(artifact_id, "SHADOW", new_status, ev, db=conn)

    def update_artifact_profile(self, artifact_id: str, profile_json: str, db=None):
        if db is not None:
            db.execute(
                "UPDATE artifacts SET profile=? WHERE id=? AND profile IS NULL AND status='SHADOW'",
                (profile_json, artifact_id),
            )
        else:
            with self.transaction() as conn:
                conn.execute(
                    "UPDATE artifacts SET profile=? WHERE id=? AND profile IS NULL AND status='SHADOW'",
                    (profile_json, artifact_id),
                )

    def update_active_artifact_profile(self, artifact_id: str, profile_json: str):
        with self.transaction() as db:
            db.execute(
                "UPDATE artifacts SET profile=? WHERE id=? AND status='ACTIVE'",
                (profile_json, artifact_id),
            )

    def update_artifact_evidence(self, artifact_id: str, evidence_json: str, db=None):
        if db is not None:
            db.execute("UPDATE artifacts SET evidence=? WHERE id=?", (evidence_json, artifact_id))
        else:
            with self.transaction() as conn:
                conn.execute("UPDATE artifacts SET evidence=? WHERE id=?", (evidence_json, artifact_id))

    def record_event(
        self,
        artifact_id: str,
        previous: str,
        current: str,
        detail_json: str,
        created: float | None = None,
        db=None,
    ):
        ts = created if created is not None else time.time()
        if db is not None:
            db.execute(
                "INSERT INTO events(artifact, created, previous, current, detail) VALUES (?,?,?,?,?)",
                (artifact_id, ts, previous, current, detail_json),
            )
        else:
            with self.transaction() as conn:
                conn.execute(
                    "INSERT INTO events(artifact, created, previous, current, detail) VALUES (?,?,?,?,?)",
                    (artifact_id, ts, previous, current, detail_json),
                )

    def get_events(self, artifact_id: str) -> list[dict]:
        return self.rows("SELECT * FROM events WHERE artifact=? ORDER BY id", (artifact_id,))

    def record_decision(
        self,
        decision_id: str,
        site_version: str,
        task: str,
        created: float,
        state_json: str,
        choice: str,
        source: str,
        reason: str | None,
        artifact_id: str | None,
        prediction_json: str | None,
        confidence: float | None,
        elapsed: float,
        usage_json: str,
        fast_served: int = 0,
    ):
        with self.transaction() as db:
            db.execute(
                "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    decision_id,
                    site_version,
                    task,
                    created,
                    state_json,
                    choice,
                    source,
                    reason,
                    artifact_id,
                    prediction_json,
                    confidence,
                    elapsed,
                    usage_json,
                ),
            )
            db.execute(
                """INSERT INTO state_coverage(site, state, observations, fast_served)
                VALUES (?,?,1,?) ON CONFLICT(site, state) DO UPDATE SET
                observations=observations+1, fast_served=fast_served+excluded.fast_served""",
                (site_version, state_json, fast_served),
            )

    def record_outcome_payload(
        self,
        decision_id: str,
        payload_json: str,
        created: float | None = None,
        quality: float | None = None,
    ) -> dict:
        ts = created if created is not None else time.time()
        with self.transaction() as db:
            dec = db.execute(
                "SELECT site, state, artifact, prediction, choice FROM decisions WHERE id=?",
                (decision_id,),
            ).fetchone()
            if dec is None:
                raise KeyError(f"Unknown decision ID: {decision_id}")
            prev = db.execute("SELECT payload FROM outcomes WHERE decision=?", (decision_id,)).fetchone()
            if prev is not None:
                raise ValueError("Outcome already recorded for decision")
            db.execute("INSERT INTO outcomes VALUES (?,?,?)", (decision_id, payload_json, ts))
            q_val = quality if quality is not None else 0.0
            db.execute(
                """INSERT INTO state_coverage(site, state, observations, outcomes, quality_sum)
                VALUES (?,?,0,1,?) ON CONFLICT(site, state) DO UPDATE SET
                outcomes=outcomes+1, quality_sum=quality_sum+excluded.quality_sum""",
                (dec["site"], dec["state"], q_val),
            )
            return dict(dec)

    def get_decision(self, decision_id: str) -> dict | None:
        rows = self.rows("SELECT * FROM decisions WHERE id=?", (decision_id,))
        return rows[0] if rows else None

    def get_outcome(self, decision_id: str) -> dict | None:
        rows = self.rows("SELECT * FROM outcomes WHERE decision=?", (decision_id,))
        return rows[0] if rows else None

    def get_site_decisions_count(self, site_version: str) -> int:
        rows = self.rows("SELECT COUNT(*) as c FROM decisions WHERE site=?", (site_version,))
        return rows[0]["c"] if rows else 0

    def get_total_fast_served(self, site_version: str) -> int:
        rows = self.rows(
            "SELECT SUM(fast_served) as fast_count FROM state_coverage WHERE site=?",
            (site_version,),
        )
        return (rows[0]["fast_count"] or 0) if rows else 0

    def get_site_contracts(self, site_name: str) -> list[dict]:
        return self.rows("SELECT contract FROM sites WHERE name=? ORDER BY created DESC", (site_name,))

    def get_state_coverage(self, site_version: str) -> list[dict]:
        return self.rows(
            "SELECT state, observations, fast_served, outcomes, quality_sum FROM state_coverage WHERE site=? ORDER BY observations DESC",
            (site_version,),
        )

    def get_drift_checks(self, site_version: str) -> list[dict]:
        return self.rows(
            "SELECT d.* FROM drift_checks d JOIN artifacts a ON a.id=d.artifact WHERE a.site=? ORDER BY d.created DESC, d.id DESC",
            (site_version,),
        )

    def get_promotion_records(self, site_version: str) -> list[dict]:
        return self.rows(
            "SELECT p.* FROM promotion_records p JOIN artifacts a ON a.id=p.artifact WHERE a.site=? ORDER BY p.decided DESC",
            (site_version,),
        )

    def get_artifact_links(self, site_version: str) -> list[dict]:
        return self.rows(
            "SELECT l.* FROM artifact_links l JOIN artifacts a ON a.id=l.child WHERE a.site=? ORDER BY l.created",
            (site_version,),
        )

    def record_drift_check(
        self,
        artifact_id: str,
        created: float,
        demoted: int,
        active_samples: int,
        comparison_samples: int,
        missing_outcomes: int,
        quality_lower: float | None,
        delta_lower: float | None,
        uncovered_rate: float | None,
        db=None,
    ):
        params = (
            artifact_id,
            created,
            demoted,
            active_samples,
            comparison_samples,
            missing_outcomes,
            quality_lower,
            delta_lower,
            uncovered_rate,
        )
        if db is not None:
            db.execute(
                """INSERT INTO drift_checks(artifact, created, demoted, active_samples,
                comparison_samples, missing_outcomes, quality_lower, delta_lower, uncovered_rate)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                params,
            )
        else:
            with self.transaction() as conn:
                conn.execute(
                    """INSERT INTO drift_checks(artifact, created, demoted, active_samples,
                    comparison_samples, missing_outcomes, quality_lower, delta_lower, uncovered_rate)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    params,
                )

    def delete_decisions_by_ids(self, ids: list[str]) -> int:
        if not ids:
            return 0
        deleted = 0
        with self.transaction() as db:
            for i in range(0, len(ids), 900):
                chunk = ids[i : i + 900]
                q = f"DELETE FROM decisions WHERE id IN ({','.join('?' for _ in chunk)})"
                deleted += db.execute(q, chunk).rowcount
        return deleted

    def commit_fast_path_decision(
        self,
        decision_id: str,
        site_version: str,
        task_id: str,
        created: float,
        state_json: str,
        choice: str,
        artifact_id: str,
        fast_path_version: str,
        prediction_json: str | None,
        confidence: float | None,
        elapsed: float,
        usage_json: str,
        expected_epoch: float,
    ):
        with self.transaction() as db:
            current = db.execute(
                "SELECT status,epoch FROM artifacts WHERE id=?", (artifact_id,)
            ).fetchone()
            if not current or current["status"] != "ACTIVE" or current["epoch"] != expected_epoch:
                raise sqlite3.OperationalError("Artifact changed during dispatch")
            db.execute(
                "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    decision_id,
                    site_version,
                    task_id,
                    created,
                    state_json,
                    choice,
                    "fast_path",
                    None,
                    fast_path_version,
                    prediction_json,
                    confidence,
                    elapsed,
                    usage_json,
                ),
            )
            db.execute(
                """INSERT INTO state_coverage(site, state, observations, fast_served)
                VALUES (?,?,1,1) ON CONFLICT(site, state) DO UPDATE SET
                observations=observations+1, fast_served=fast_served+1""",
                (site_version, state_json),
            )

    def commit_fallback_decision(
        self,
        decision_id: str,
        site_version: str,
        task_id: str,
        created: float,
        state_json: str,
        choice: str,
        reason: str | None,
        artifact_id: str | None,
        fast_path_version: str | None,
        prediction_json: str | None,
        confidence: float | None,
        elapsed: float,
        usage_json: str,
    ):
        with self.transaction() as db:
            db.execute(
                "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    decision_id,
                    site_version,
                    task_id,
                    created,
                    state_json,
                    choice,
                    "fallback",
                    reason,
                    fast_path_version,
                    prediction_json,
                    confidence,
                    elapsed,
                    usage_json,
                ),
            )
            db.execute(
                """INSERT INTO state_coverage(site, state, observations, fast_served)
                VALUES (?,?,1,0) ON CONFLICT(site, state) DO UPDATE SET
                observations=observations+1""",
                (site_version, state_json),
            )

    def record_outcome_transaction(
        self,
        decision_id: str,
        payload_json: str,
        quality: float,
    ) -> dict | None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT site, state, artifact, prediction, choice FROM decisions WHERE id=?",
                (decision_id,),
            ).fetchone()
            if not row:
                return None
            existing = db.execute(
                "SELECT payload FROM outcomes WHERE decision=?", (decision_id,)
            ).fetchone()
            if existing:
                if existing[0] != payload_json:
                    raise ValueError("Conflicting outcome; recorded evidence is immutable")
                return dict(row)
            db.execute(
                "INSERT INTO outcomes VALUES (?,?,?)", (decision_id, payload_json, time.time())
            )
            db.execute(
                """INSERT INTO state_coverage(site, state, observations, outcomes, quality_sum)
                VALUES (?,?,0,1,?) ON CONFLICT(site, state) DO UPDATE SET
                outcomes=outcomes+1, quality_sum=quality_sum+excluded.quality_sum""",
                (row["site"], row["state"], quality),
            )
            return dict(row)

    def get_open_epoch(self, artifact_id: str, app_id: str = "default") -> dict | None:
        rows = self.rows(
            "SELECT epoch_id, requirements FROM evidence_epochs "
            "WHERE artifact_id=? AND app_id=? AND status='OPEN' "
            "ORDER BY created DESC LIMIT 1",
            (artifact_id, app_id),
        )
        return rows[0] if rows else None

    def compile_candidate_artifact(
        self,
        artifact_id: str,
        site_version: str,
        payload_json: str,
        checksum: str,
        engine_key: str,
        replace_existing: bool = False,
    ):
        now = time.time()
        with self.transaction() as db:
            previous = db.execute(
                """SELECT id, status FROM artifacts WHERE site=? AND status!='RETIRED'
                AND json_extract(payload, '$.engine_data.engine')=?""",
                (site_version, engine_key),
            ).fetchone()
            if previous:
                if not replace_existing:
                    raise ValueError("Site already has a candidate; explicitly replace to recompile")
                db.execute("UPDATE artifacts SET status='RETIRED' WHERE id=?", (previous["id"],))
                self.record_event(
                    previous["id"],
                    previous["status"],
                    "RETIRED",
                    json.dumps({"replaced_by": artifact_id}),
                    db=db,
                )
            db.execute(
                "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
                (
                    artifact_id,
                    site_version,
                    payload_json,
                    checksum,
                    "CANDIDATE",
                    now,
                    None,
                    None,
                ),
            )
            if previous:
                db.execute(
                    "INSERT OR IGNORE INTO artifact_links VALUES (?,?,?)",
                    (previous["id"], artifact_id, now),
                )
            self.record_event(artifact_id, "OBSERVE", "CANDIDATE", json.dumps({"engine": engine_key}), db=db)
            db.execute("UPDATE artifacts SET status='SHADOW' WHERE id=?", (artifact_id,))
            self.record_event(artifact_id, "CANDIDATE", "SHADOW", "{}", db=db)

    def save_policy_observation(
        self,
        *,
        decision_id: str,
        site_version: str,
        state: dict,
        host_choice: str,
        proposed_choice: str,
        scores: dict,
        representation: list[float] | None,
        confidence: float,
        ambiguity: float | None,
        act_probability: float | None,
        checkpoint_identity: str,
        created: float | None = None,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                """INSERT OR IGNORE INTO policy_observations(
                decision,site,state,host_choice,proposed_choice,scores,representation,
                confidence,ambiguity,act_probability,checkpoint_identity,created)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    decision_id,
                    site_version,
                    canonical(state),
                    host_choice,
                    proposed_choice,
                    canonical(scores),
                    canonical(representation) if representation is not None else None,
                    confidence,
                    ambiguity,
                    act_probability,
                    checkpoint_identity,
                    created if created is not None else time.time(),
                ),
            )

    def attach_policy_outcome(self, decision_id: str, outcome: dict) -> None:
        with self.transaction() as db:
            db.execute(
                """UPDATE policy_observations SET verified_outcome=?,verified_at=?
                WHERE decision=?""",
                (canonical(outcome), time.time(), decision_id),
            )

    def get_policy_observations(
        self, site_version: str, *, verified_only: bool = False
    ) -> list[dict]:
        suffix = " AND verified_outcome IS NOT NULL" if verified_only else ""
        rows = self.rows(
            "SELECT * FROM policy_observations WHERE site=?" + suffix + " ORDER BY created,decision",
            (site_version,),
        )
        for row in rows:
            for key in ("state", "scores", "representation", "verified_outcome"):
                row[key] = json.loads(row[key]) if row.get(key) else None
        return rows

    def record_authority_event(
        self,
        *,
        step: int,
        site: str,
        engine: str,
        artifact_id: str,
        previous_status: str,
        new_status: str,
        support_n: int,
        quality: float | None,
        lower_bound: float | None,
        coverage: float | None,
        reason: str,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                """INSERT INTO authority_events(step,site,engine,artifact_id,
                previous_status,new_status,support_n,quality,lower_bound,coverage,reason,created)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    step, site, engine, artifact_id, previous_status, new_status,
                    support_n, quality, lower_bound, coverage, reason, time.time(),
                ),
            )

    def authority_events(self, site_version: str) -> list[dict]:
        return self.rows(
            "SELECT * FROM authority_events WHERE site=? ORDER BY step,id", (site_version,)
        )

    def record_non_promotion(
        self, *, step: int, site: str, engine: str, artifact_id: str,
        reason: str, detail: dict | None = None
    ) -> None:
        allowed = {
            "insufficient support", "quality bound failed", "degradation bound failed",
            "region purity failed", "ambiguity", "OOD", "insufficient comparisons",
        }
        if reason not in allowed:
            raise ValueError(f"Unknown non-promotion reason: {reason}")
        with self.transaction() as db:
            db.execute(
                """INSERT INTO non_promotion_events(step,site,engine,artifact_id,reason,detail,created)
                VALUES (?,?,?,?,?,?,?)""",
                (step, site, engine, artifact_id, reason, canonical(detail or {}), time.time()),
            )

    def calibrate_candidate_artifact(self, artifact_id: str, profile_json: str):
        with self.transaction() as db:
            count = db.execute(
                "UPDATE artifacts SET profile=? WHERE id=? AND profile IS NULL AND status='SHADOW'",
                (profile_json, artifact_id),
            ).rowcount
            if not count:
                raise ValueError("Candidate changed during calibration")

    def promote_candidate_artifact(
        self,
        artifact_id: str,
        expected_epoch: float,
        evidence_json: str,
        promotion_record: tuple,
        qualified: bool,
        auto_promote: bool,
        profile_id: str,
    ):
        now = time.time()
        with self.transaction() as db:
            current = db.execute(
                "SELECT status,epoch FROM artifacts WHERE id=?", (artifact_id,)
            ).fetchone()
            if not current or current["status"] != "SHADOW" or current["epoch"] != expected_epoch:
                raise ValueError("Candidate changed during evaluation")
            db.execute(
                "UPDATE artifacts SET evidence=? WHERE id=?", (evidence_json, artifact_id)
            )
            db.execute(
                "INSERT OR REPLACE INTO promotion_records VALUES (?,?,?,?,?,?,?,?)",
                promotion_record,
            )
            if qualified:
                self.record_event(artifact_id, "SHADOW", "VERIFIED", evidence_json, db=db)
                if auto_promote:
                    self.record_event(
                        artifact_id, "VERIFIED", "ACTIVE", json.dumps({"profile": profile_id}), db=db
                    )
                    db.execute(
                        "UPDATE artifacts SET status='ACTIVE',epoch=? WHERE id=?",
                        (now, artifact_id),
                    )
                else:
                    db.execute("UPDATE artifacts SET status='VERIFIED' WHERE id=?", (artifact_id,))

    def demote_active_artifact(
        self,
        artifact_id: str,
        expected_epoch: float,
        evidence_json: str,
    ) -> bool:
        now = time.time()
        with self.transaction() as db:
            count = db.execute(
                "UPDATE artifacts SET status='SHADOW',epoch=? WHERE id=? AND status='ACTIVE' AND epoch=?",
                (now, artifact_id, expected_epoch),
            ).rowcount
            if count:
                self.revoke_epoch_and_regions(artifact_id, db=db)
                self.record_event(artifact_id, "ACTIVE", "SHADOW", evidence_json, db=db)
                return True
            return False

    def invalidate_artifact(
        self, artifact_id: str, current_status: str, target_status: str, reason: str
    ):
        now = time.time()
        with self.transaction() as db:
            db.execute(
                "UPDATE artifacts SET status=?, epoch=? WHERE id=?",
                (target_status, now, artifact_id),
            )
            self.record_event(
                artifact_id,
                current_status,
                target_status,
                json.dumps({"reason": reason, "explicit": True}),
                db=db,
            )

    def hot_swap_artifact(self, site_version: str, new_artifact_id: str) -> str:
        now = time.time()
        with self.transaction() as db:
            old_art = db.execute(
                "SELECT id, status, epoch FROM artifacts WHERE site=? AND status='ACTIVE'",
                (site_version,),
            ).fetchone()
            new_art = db.execute(
                "SELECT id, status FROM artifacts WHERE id=?", (new_artifact_id,)
            ).fetchone()
            if not new_art or new_art["status"] not in ("VERIFIED", "SHADOW"):
                raise ValueError("Replacement artifact must be in VERIFIED or SHADOW state")
            if old_art:
                db.execute("UPDATE artifacts SET status='RETIRED' WHERE id=?", (old_art["id"],))
                self.record_event(
                    old_art["id"],
                    "ACTIVE",
                    "RETIRED",
                    json.dumps({"replaced_by": new_artifact_id}),
                    db=db,
                )
            db.execute(
                "UPDATE artifacts SET status='ACTIVE', epoch=? WHERE id=?",
                (now, new_artifact_id),
            )
            self.record_event(
                new_artifact_id,
                new_art["status"],
                "ACTIVE",
                json.dumps({"replaces": old_art["id"] if old_art else None, "hot_swapped": True}),
                db=db,
            )
            if old_art:
                db.execute(
                    "INSERT OR IGNORE INTO artifact_links VALUES (?,?,?)",
                    (old_art["id"], new_artifact_id, now),
                )
        return new_artifact_id

    def compact_decisions(
        self, site_version: str, keep_recent: int, cutoff_time: float, protected_ids: set[str]
    ) -> tuple[int, int]:
        with self.transaction() as db:
            recent_ids = {
                r[0]
                for r in db.execute(
                    "SELECT id FROM decisions WHERE site=? ORDER BY created DESC LIMIT ?",
                    (site_version, keep_recent),
                ).fetchall()
            }
            all_protected = protected_ids | recent_ids
            rows_to_prune = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM decisions WHERE site=? AND created < ?",
                    (site_version, cutoff_time),
                ).fetchall()
                if r[0] not in all_protected
            ]
            if rows_to_prune:
                chunk_size = 500
                for i in range(0, len(rows_to_prune), chunk_size):
                    chunk = rows_to_prune[i : i + chunk_size]
                    q = f"DELETE FROM decisions WHERE id IN ({','.join('?' for _ in chunk)})"
                    db.execute(q, chunk)
            remaining = db.execute(
                "SELECT COUNT(*) FROM decisions WHERE site=?", (site_version,)
            ).fetchone()[0]
            self._rebuild_coverage(db, site_version)
            return len(rows_to_prune), remaining

    def compact_site_decisions(
        self,
        site_version: str,
        keep_recent: int = 1000,
        before_timestamp: float | None = None,
    ) -> tuple[int, int]:
        active = self.get_active_artifact(site_version)
        shadows = self.rows(
            "SELECT id, epoch, payload FROM artifacts WHERE site=? AND status IN ('SHADOW','CANDIDATE')",
            (site_version,),
        )
        protected_ids = set()
        for art in ([active] if active else []) + (shadows or []):
            try:
                p_load = json.loads(art["payload"]) if isinstance(art["payload"], str) else art["payload"]
                for part_ids in p_load.get("partitions", {}).values():
                    protected_ids.update(part_ids)
            except (json.JSONDecodeError, TypeError, KeyError):
                pass

        min_epoch = active["epoch"] if active else None
        for s in (shadows or []):
            if min_epoch is None or s["epoch"] < min_epoch:
                min_epoch = s["epoch"]

        cutoff = before_timestamp if before_timestamp is not None else (min_epoch or time.time())
        pruned_count, remaining = self.compact_decisions(site_version, keep_recent, cutoff, protected_ids)
        if active:
            self.record_event(
                active["id"],
                "ACTIVE",
                "ACTIVE",
                json.dumps({"event": "compaction", "pruned": pruned_count, "remaining": remaining}),
            )
        return pruned_count, remaining

    def get_latest_drift_check(self, artifact_id: str) -> dict | None:
        rows = self.rows(
            "SELECT demoted, delta_lower, missing_outcomes FROM drift_checks "
            "WHERE artifact=? ORDER BY created DESC, id DESC LIMIT 1",
            (artifact_id,),
        )
        return rows[0] if rows else None

    def get_doctor_summary(self) -> dict:
        with self.lock:
            integrity = self.conn.execute("PRAGMA integrity_check").fetchone()[0]
            user_ver = self.conn.execute("PRAGMA user_version").fetchone()[0]
            sites_cnt = self.conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
            active_cnt = self.conn.execute(
                "SELECT COUNT(*) FROM artifacts WHERE status='ACTIVE'"
            ).fetchone()[0]
            epochs_cnt = self.conn.execute(
                "SELECT COUNT(*) FROM evidence_epochs WHERE status='OPEN'"
            ).fetchone()[0]
            return {
                "integrity": integrity,
                "user_version": user_ver,
                "sites_count": sites_cnt,
                "active_count": active_cnt,
                "epochs_count": epochs_cnt,
            }

    def save_policy_utility(
        self,
        *,
        site_key: str,
        site_version: str,
        checkpoint_revision: str,
        state: str,
        evidence: dict[str, Any],
        updated_at: float | None = None,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                """INSERT OR REPLACE INTO policy_utility_evidence(
                   site_key, site_version, checkpoint_revision, state, evidence_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    site_key,
                    site_version,
                    checkpoint_revision,
                    state,
                    canonical(evidence),
                    updated_at if updated_at is not None else time.time(),
                ),
            )

    def get_policy_utility(self, site_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT site_key, site_version, checkpoint_revision, state, evidence_json, updated_at "
            "FROM policy_utility_evidence WHERE site_key = ?",
            (site_key,),
        ).fetchone()
        if row is None:
            return None
        return {
            "site_key": row["site_key"],
            "site_version": row["site_version"],
            "checkpoint_revision": row["checkpoint_revision"],
            "state": row["state"],
            "evidence": json.loads(row["evidence_json"]),
            "updated_at": row["updated_at"],
        }

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
