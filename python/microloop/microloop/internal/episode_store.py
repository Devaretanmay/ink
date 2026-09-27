"""Local episode store: the beginning of the Microloop dataset.

A single SQLite file under ``.microloop/`` records one row per episode, one per
adaptation, and one per candidate the scored controller considered. It is local
only: no server, no account, no telemetry. The point is to be able to ask, after
a few hundred runs, which adaptations actually helped -- and under what
conditions -- without having to re-run anything.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runtime.episode import Episode

__all__ = ["DEFAULT_DB_PATH", "EpisodeStore"]

DEFAULT_DB_PATH = ".microloop/episodes.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    task TEXT,
    arm TEXT,
    run_mode TEXT,
    goal TEXT,
    outcome TEXT,
    success INTEGER,
    verifier TEXT,
    score REAL,
    steps INTEGER,
    cost REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    elapsed_seconds REAL,
    model_calls INTEGER,
    created_at TEXT,
    summary_json TEXT
);
CREATE TABLE IF NOT EXISTS adaptations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episodes(id),
    action TEXT,
    step INTEGER,
    before_json TEXT,
    after_json TEXT,
    outcome TEXT,
    applied INTEGER,
    adapter_reason TEXT
);
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episodes(id),
    step INTEGER,
    action TEXT,
    score REAL,
    selected INTEGER,
    expected_progress REAL,
    cost_penalty REAL,
    repetition_penalty REAL,
    risk_penalty REAL
);
CREATE INDEX IF NOT EXISTS adaptations_episode ON adaptations(episode_id);
CREATE INDEX IF NOT EXISTS candidates_episode ON candidates(episode_id);
"""

#: Adaptation outcomes that count as a success for a given action.
_OUTCOMES = ("improved", "no_change", "regressed", "pending")


class EpisodeStore:
    """A local SQLite store for episodes, adaptations and controller traces."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = str(path) if path is not None else DEFAULT_DB_PATH
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created.

        ``CREATE TABLE IF NOT EXISTS`` silently leaves an older file without the
        new columns, and the failure then surfaces as an opaque "no such column"
        at insert time. A few lines here beat that.
        """
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(episodes)")}
        if "model_calls" not in existing:
            self._conn.execute("ALTER TABLE episodes ADD COLUMN model_calls INTEGER")

    # -- writes ---------------------------------------------------------------
    def record(
        self,
        episode: Episode | dict[str, Any],
        *,
        run_id: str | None = None,
        task: str | None = None,
        arm: str | None = None,
        run_mode: str = "real",
        success: bool | None = None,
        verifier: str | None = None,
        score: float | None = None,
    ) -> int:
        """Persist one episode and return its row id."""
        summary = episode.summary() if isinstance(episode, Episode) else dict(episode)
        usage = summary.get("usage") or {}
        cost = usage.get("cost")
        if success is not None:
            resolved_success = bool(success)
        else:
            resolved_success = summary.get("outcome") == "completed"
        cursor = self._conn.execute(
            """
            INSERT INTO episodes (
                run_id, task, arm, run_mode, goal, outcome, success, verifier, score,
                steps, cost, input_tokens, output_tokens, elapsed_seconds, model_calls,
                created_at, summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                task,
                arm,
                run_mode,
                summary.get("goal"),
                summary.get("outcome"),
                1 if resolved_success else 0,
                verifier,
                score,
                summary.get("steps"),
                cost,
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                usage.get("elapsed_seconds"),
                usage.get("model_calls"),
                datetime.now(timezone.utc).isoformat(),
                json.dumps(summary),
            ),
        )
        episode_id = int(cursor.lastrowid)
        for record in summary.get("adaptations", []):
            self._conn.execute(
                """
                INSERT INTO adaptations (
                    episode_id, action, step, before_json, after_json, outcome,
                    applied, adapter_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode_id,
                    record.get("action"),
                    record.get("step"),
                    json.dumps(record.get("before")) if record.get("before") else None,
                    json.dumps(record.get("after")) if record.get("after") else None,
                    record.get("result") or record.get("outcome"),
                    1 if record.get("applied") else 0,
                    record.get("adapter_reason"),
                ),
            )
            self._record_candidates(episode_id, record.get("trace"))
        self._conn.commit()
        return episode_id

    def _record_candidates(self, episode_id: int, trace: Any) -> None:
        if not isinstance(trace, dict):
            return
        selected = trace.get("selected")
        for candidate in trace.get("candidates", []):
            self._conn.execute(
                """
                INSERT INTO candidates (
                    episode_id, step, action, score, selected, expected_progress,
                    cost_penalty, repetition_penalty, risk_penalty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode_id,
                    candidate.get("step"),
                    candidate.get("action"),
                    candidate.get("score"),
                    1 if candidate.get("action") == selected else 0,
                    candidate.get("expected_progress"),
                    candidate.get("cost_penalty"),
                    candidate.get("repetition_penalty"),
                    candidate.get("risk_penalty"),
                ),
            )

    # -- reads ----------------------------------------------------------------
    def episodes(self) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM episodes ORDER BY id"))

    def adaptations(self, episode_id: int | None = None) -> list[sqlite3.Row]:
        if episode_id is None:
            return list(self._conn.execute("SELECT * FROM adaptations ORDER BY id"))
        return list(
            self._conn.execute(
                "SELECT * FROM adaptations WHERE episode_id = ? ORDER BY id", (episode_id,)
            )
        )

    def candidates(self, episode_id: int | None = None) -> list[sqlite3.Row]:
        if episode_id is None:
            return list(self._conn.execute("SELECT * FROM candidates ORDER BY id"))
        return list(
            self._conn.execute(
                "SELECT * FROM candidates WHERE episode_id = ? ORDER BY id", (episode_id,)
            )
        )

    def stats(self) -> dict[str, Any]:
        """Aggregate the dataset: outcomes per action, per arm and cost per success."""
        episodes = self.episodes()
        by_action: dict[str, dict[str, int]] = {}
        for action, outcome, applied in self._conn.execute(
            "SELECT action, outcome, applied FROM adaptations"
        ):
            bucket = by_action.setdefault(
                action, {"attempted": 0, "applied": 0, **{name: 0 for name in _OUTCOMES}}
            )
            bucket["attempted"] += 1
            if applied:
                bucket["applied"] += 1
            if outcome in bucket:
                bucket[outcome] += 1
            else:
                bucket["pending"] += 1

        arms: dict[str, dict[str, Any]] = {}
        for row in episodes:
            arm = row["arm"] or "unlabeled"
            bucket = arms.setdefault(arm, {"episodes": 0, "successful": 0, "cost": 0.0})
            bucket["episodes"] += 1
            bucket["successful"] += 1 if row["success"] else 0
            bucket["cost"] += row["cost"] or 0.0
        for bucket in arms.values():
            bucket["cost"] = round(bucket["cost"], 4)
            bucket["cost_per_success"] = (
                round(bucket["cost"] / bucket["successful"], 4)
                if bucket["successful"]
                else None
            )

        successful = sum(1 for row in episodes if row["success"])
        total_cost = sum(row["cost"] or 0.0 for row in episodes)
        return {
            "episodes": len(episodes),
            "successful": successful,
            "adaptations": sum(bucket["attempted"] for bucket in by_action.values()),
            "by_action": by_action,
            "arms": arms,
            "segments": self.segmented(),
            "attribution": self.attribution(),
            "cost_per_success": (
                round(total_cost / successful, 4) if successful else None
            ),
        }

    def segmented(self) -> dict[str, Any]:
        """Segment adaptation outcomes by the situation they were chosen in.

        Aggregate counts answer "did replan work?" and that question is almost
        always too coarse: an adaptation helps in one stall and hurts in another,
        and averaging the two hides the distinction. This groups by progress
        state and the signals present, so ``stalled + recurrent_error`` can be
        compared against ``stalled`` on its own.
        """
        segments: dict[tuple[str, tuple[str, ...], str], dict[str, int]] = {}
        for action, before_json, outcome in self._conn.execute(
            "SELECT action, before_json, outcome FROM adaptations"
        ):
            before = json.loads(before_json) if before_json else {}
            state = str(before.get("progress") or "unknown")
            signals = tuple(sorted(str(name) for name in before.get("signals") or ()))
            key = (state, signals, str(action or "unknown"))
            bucket = segments.setdefault(key, {"attempted": 0, "improved": 0, "no_change": 0})
            bucket["attempted"] += 1
            if outcome in bucket:
                bucket[outcome] += 1

        grouped: dict[str, list[dict[str, Any]]] = {}
        for (state, signals, action), bucket in segments.items():
            attempted = bucket["attempted"]
            grouped.setdefault(state, []).append(
                {
                    "action": action,
                    "signals": list(signals),
                    "attempted": attempted,
                    "improved": bucket["improved"],
                    "no_change": bucket["no_change"],
                    "improved_rate": round(bucket["improved"] / attempted, 4)
                    if attempted
                    else None,
                }
            )
        for rows in grouped.values():
            rows.sort(key=lambda row: (-row["attempted"], row["action"]))
        return grouped

    def attribution(self) -> dict[str, Any]:
        """Which actions are associated with runs that actually finished.

        :meth:`stats` judges an adaptation by whether progress improved afterwards,
        and that label turns out to be almost useless on its own: in a real run
        roughly 95% of replans were scored ``improved`` while the adaptive arm
        *lost* to doing nothing. Progress recovers locally and the task still
        fails, so the label measures the detector rather than the outcome.

        This groups by whether the run succeeded instead, and includes a control
        bucket of episodes with no adaptation at all. The comparison is
        associative, not causal -- an action that fires on hard tasks will look
        bad here for that reason alone -- which is why the control is reported
        beside it rather than instead of it.
        """
        rows = list(
            self._conn.execute(
                """
                SELECT a.action AS action, e.success AS success
                FROM adaptations a
                JOIN episodes e ON e.id = a.episode_id
                """
            )
        )
        unadapted = list(
            self._conn.execute(
                """
                SELECT e.success AS success
                FROM episodes e
                WHERE NOT EXISTS (
                    SELECT 1 FROM adaptations a WHERE a.episode_id = e.id
                )
                """
            )
        )

        def rate(rows_: list[Any]) -> float | None:
            if not rows_:
                return None
            return round(sum(1 for r in rows_ if r["success"]) / len(rows_), 4)

        buckets: dict[str, list[Any]] = {}
        for row in rows:
            buckets.setdefault(str(row["action"] or "unknown"), []).append(row)
        return {
            "control_no_adaptation": {
                "episodes": len(unadapted),
                "success_rate": rate(unadapted),
            },
            "by_action": {
                action: {
                    "episodes": len(group),
                    "success_rate": rate(group),
                }
                for action, group in sorted(buckets.items())
            },
        }

    def export_jsonl(self, path: str | Path) -> int:
        """Write one JSON object per episode (with its adaptations) and return the count."""
        written = 0
        with open(path, "w", encoding="utf-8") as handle:
            for row in self.episodes():
                payload = {
                    "run_id": row["run_id"],
                    "task": row["task"],
                    "arm": row["arm"],
                    "run_mode": row["run_mode"],
                    "success": bool(row["success"]),
                    "verifier": row["verifier"],
                    "score": row["score"],
                    "created_at": row["created_at"],
                    "summary": json.loads(row["summary_json"]) if row["summary_json"] else {},
                }
                handle.write(json.dumps(payload) + "\n")
                written += 1
        return written

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> EpisodeStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
