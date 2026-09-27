"""Explicit bounded decisions and evidence-driven local fast paths."""

from __future__ import annotations

import inspect
import json
import math
import secrets
import sqlite3
import time
import uuid
from collections import Counter
from dataclasses import asdict

from .internal.contracts import (
    DecisionResult,
    DecisionSite,
    FallbackResult,
    Outcome,
    PromotionRequirements,
    canonical,
    digest,
)
from .internal.decision_store import DecisionStore, split_history
from .internal.engines import ExactEngine, LayaEngine
from .internal.profiler import profile_history
from .internal.verification import grouped_quality, lower_bound, passes, statistics, verify_rows


class Microloop:
    def __init__(self, path=".microloop/decisions.db", *, engines=(), readonly=False):
        self.store = DecisionStore(path, readonly=readonly)
        self.engines = {e.name: e for e in (ExactEngine(), LayaEngine(), *engines)}

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def register(self, site: DecisionSite):
        # Round-trip copies mutable schema inputs before they become persistent contracts.
        contract = canonical(asdict(site))
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO sites VALUES (?,?,?,?)",
                (site.version, site.name, contract, time.time()),
            )
        return site

    def _site(self, site, state, choices, fallback_revision):
        if isinstance(site, DecisionSite):
            if choices is not None and tuple(choices) != site.choices:
                raise ValueError("Choices disagree with registered contract")
            try:
                return self.register(site)
            except sqlite3.Error:
                # Explicit contracts still allow the original agent to operate during an outage.
                return site
        existing = self.store.rows(
            "SELECT contract FROM sites WHERE name=? ORDER BY created DESC", (site,)
        )
        if existing:
            contract = json.loads(existing[0]["contract"])
            if choices is not None:
                contract["choices"] = choices
            if fallback_revision is not None:
                contract["fallback_revision"] = fallback_revision
            return self.register(DecisionSite(**contract))
        if choices is None:
            raise ValueError("A new site requires choices or explicit registration")
        types = {str: "string", int: "integer", float: "number", bool: "boolean"}
        try:
            schema = {key: types[type(value)] for key, value in state.items()}
        except KeyError as error:
            raise ValueError(
                "Use DecisionSite with an explicit schema for optional fields"
            ) from error
        return self.register(DecisionSite(site, schema, tuple(choices), fallback_revision or "1"))

    def _artifact(self, site):
        rows = self.store.rows(
            "SELECT * FROM artifacts WHERE site=? AND status!='RETIRED'", (site,)
        )
        if not rows:
            return None
        row = rows[0]
        row["payload"] = json.loads(row["payload"])
        if digest(row["payload"]) != row["checksum"]:
            raise ValueError("Artifact integrity mismatch")
        row["profile"] = json.loads(row["profile"]) if row["profile"] else None
        if row["profile"]:
            profile = dict(row["profile"])
            profile_hash = profile.pop("id")
            if digest(profile) != profile_hash:
                raise ValueError("Profile integrity mismatch")
        return row

    def _route(self, site, state):
        artifact = None
        try:
            artifact = self._artifact(site.version)
            if artifact is None:
                return None, None, "observe"
            payload = artifact["payload"]
            coverage = (
                artifact["profile"]["coverage"] if artifact["profile"] else payload["coverage"]
            )
            region = coverage.get(canonical(state))
            if region is None:
                return artifact, None, "outside_coverage"
            engine = self.engines[payload["engine_data"]["engine"]]
            choice, probability = engine.predict(payload["engine_data"], state)
            if (
                choice not in site.choices
                or not math.isfinite(probability)
                or not 0 <= probability <= 1
            ):
                raise ValueError("Invalid prediction")
            prediction = {
                "choice": choice,
                "raw_probability": probability,
                "confidence": region.get("confidence", 0.0),
            }
            profile = artifact["profile"]
            if artifact["status"] != "ACTIVE" or profile is None:
                return artifact, prediction, "shadow"
            if (
                choice != region.get("choice")
                or prediction["confidence"] < profile["requirements"]["min_confidence"]
            ):
                return artifact, prediction, "insufficient_confidence"
            if secrets.randbelow(10**9) / 10**9 < profile["requirements"]["comparison_rate"]:
                return artifact, prediction, "comparison"
            return artifact, prediction, None
        except Exception:
            # Engine exceptions must not turn a valid agent decision into a failed task.
            return artifact, None, "engine_or_store_unavailable"

    def _save(self, site, state, value, task_id, started, artifact, prediction, reason):
        fallback = value if isinstance(value, FallbackResult) else FallbackResult(value)
        if fallback.choice not in site.choices:
            raise ValueError("Fallback returned an undeclared choice")
        source = "fast_path" if reason is None else "fallback"
        result = DecisionResult(
            fallback.choice,
            uuid.uuid4().hex,
            source,
            site.version,
            artifact["id"] if artifact else None,
            reason,
            prediction["confidence"] if prediction else None,
        )
        usage = asdict(fallback) if source == "fallback" else {}
        with self.store.transaction() as db:
            if source == "fast_path":
                current = db.execute(
                    "SELECT status,epoch FROM artifacts WHERE id=?", (artifact["id"],)
                ).fetchone()
                if current["status"] != "ACTIVE" or current["epoch"] != artifact["epoch"]:
                    raise sqlite3.OperationalError("Artifact changed during dispatch")
            db.execute(
                "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    result.decision_id,
                    site.version,
                    task_id or result.decision_id,
                    time.time(),
                    canonical(state),
                    result.choice,
                    source,
                    reason,
                    result.fast_path_version,
                    canonical(prediction) if prediction else None,
                    result.confidence,
                    time.perf_counter() - started,
                    canonical(usage),
                ),
            )
        return result

    def _save_fallback(self, site, state, value, task_id, started, artifact, prediction, reason):
        try:
            return self._save(site, state, value, task_id, started, artifact, prediction, reason)
        except sqlite3.Error:
            choice = value.choice if isinstance(value, FallbackResult) else value
            if choice not in site.choices:
                raise ValueError("Fallback returned an undeclared choice") from None
            return DecisionResult(
                choice,
                uuid.uuid4().hex,
                "fallback",
                site.version,
                fallback_reason="storage_unavailable",
                recorded=False,
            )

    def decide(self, *, site, state, fallback, choices=None, task_id=None, fallback_revision=None):
        started = time.perf_counter()
        site = self._site(site, state, choices, fallback_revision)
        state = site.encode(state)
        artifact, prediction, reason = self._route(site, state)
        if reason is None:
            try:
                return self._save(
                    site, state, prediction["choice"], task_id, started, artifact, prediction, None
                )
            except sqlite3.Error:
                reason = "storage_unavailable"
        value = fallback()
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            raise TypeError("Async fallbacks require decide_async")
        return self._save_fallback(
            site, state, value, task_id, started, artifact, prediction, reason
        )

    async def decide_async(
        self, *, site, state, fallback, choices=None, task_id=None, fallback_revision=None
    ):
        started = time.perf_counter()
        site = self._site(site, state, choices, fallback_revision)
        state = site.encode(state)
        artifact, prediction, reason = self._route(site, state)
        if reason is None:
            try:
                return self._save(
                    site, state, prediction["choice"], task_id, started, artifact, prediction, None
                )
            except sqlite3.Error:
                reason = "storage_unavailable"
        value = await fallback()
        return self._save_fallback(
            site, state, value, task_id, started, artifact, prediction, reason
        )

    def record_outcome(self, decision_id, *, quality, verifier, verifier_version, evidence):
        outcome = Outcome(quality, verifier, verifier_version, evidence)
        payload = canonical(asdict(outcome))
        with self.store.transaction() as db:
            if not db.execute("SELECT 1 FROM decisions WHERE id=?", (decision_id,)).fetchone():
                raise sqlite3.IntegrityError("FOREIGN KEY constraint failed: unknown decision")
            existing = db.execute(
                "SELECT payload FROM outcomes WHERE decision=?", (decision_id,)
            ).fetchone()
            if existing and existing[0] != payload:
                raise ValueError("Conflicting outcome; recorded evidence is immutable")
            if not existing:
                db.execute(
                    "INSERT INTO outcomes VALUES (?,?,?)", (decision_id, payload, time.time())
                )
        return outcome

    def _resolve(self, site):
        if isinstance(site, DecisionSite):
            return site
        rows = self.store.rows(
            "SELECT contract FROM sites WHERE name=? ORDER BY created DESC", (site,)
        )
        if not rows:
            raise KeyError(f"Unknown decision site: {site}")
        return DecisionSite(**json.loads(rows[0]["contract"]))

    @staticmethod
    def _event(db, artifact, previous, current, detail):
        db.execute(
            "INSERT INTO events(artifact,created,previous,current,detail) VALUES (?,?,?,?,?)",
            (artifact, time.time(), previous, current, canonical(detail)),
        )

    def compile(self, site, *, engine="exact", replace_existing=False):
        site = self._resolve(site)
        rows = self.store.history(site.version)
        # Candidate labels come only from executed fallback choices with outcomes.
        eligible = [r for r in rows if r["source"] == "fallback" and r["outcome"] is not None]
        train, calibration, evaluation = split_history(eligible)
        if not all((train, calibration, evaluation)):
            raise ValueError("Need outcome-bearing task groups in all three partitions")
        payload = {
            "site": site.version,
            "choices": list(site.choices),
            "engine_data": self.engines[engine].compile(site, train),
            "coverage": {canonical(r["state"]): {} for r in train},
            "partitions": {
                name: [r["id"] for r in part]
                for name, part in (
                    ("train", train),
                    ("calibration", calibration),
                    ("evaluation", evaluation),
                )
            },
            "dataset_digest": digest(eligible),
            "created": time.time(),
        }
        artifact_id = digest(payload)
        with self.store.transaction() as db:
            previous = db.execute(
                "SELECT id,status FROM artifacts WHERE site=? AND status!='RETIRED'",
                (site.version,),
            ).fetchone()
            if previous:
                if not replace_existing:
                    raise ValueError(
                        "Site already has a candidate; explicitly replace to recompile"
                    )
                db.execute("UPDATE artifacts SET status='RETIRED' WHERE id=?", (previous["id"],))
                self._event(db, previous["id"], previous["status"], "RETIRED",
                            {"replaced_by": artifact_id})
            db.execute(
                "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
                (
                    artifact_id,
                    site.version,
                    canonical(payload),
                    digest(payload),
                    "CANDIDATE",
                    time.time(),
                    None,
                    None,
                ),
            )
            self._event(db, artifact_id, "OBSERVE", "CANDIDATE", {"engine": engine})
            db.execute("UPDATE artifacts SET status='SHADOW' WHERE id=?", (artifact_id,))
            self._event(db, artifact_id, "CANDIDATE", "SHADOW", {})
        return artifact_id

    def calibrate(self, site, *, verifier, requirements: PromotionRequirements):
        site = self._resolve(site)
        artifact = self._artifact(site.version)
        if artifact is None or artifact["status"] != "SHADOW" or artifact["profile"]:
            raise ValueError("Calibration requires an uncalibrated shadow candidate")
        payload = artifact["payload"]
        ids = set(payload["partitions"]["calibration"])
        rows = [r for r in self.store.history(site.version) if r["id"] in ids]
        records = verify_rows(
            rows, self.engines[payload["engine_data"]["engine"]], payload, verifier
        )
        regions = {}
        for record in records:
            regions.setdefault(record["region"], []).append(record)
        coverage = {}
        for key, values in regions.items():
            stats = statistics(values)
            choices = {r["choice"] for r in values}
            if (
                stats["samples"] >= requirements.min_region_samples
                and len(choices) == 1
                and stats["quality_lower"] >= requirements.min_confidence
            ):
                coverage[key] = {
                    "confidence": stats["quality_lower"],
                    "choice": values[0]["choice"],
                    "samples": stats["samples"],
                }
        if not coverage:
            raise ValueError("No state regions have sufficient calibration evidence")
        profile = {
            "requirements": asdict(requirements),
            "coverage": coverage,
            "calibration_evidence": records,
            "created": time.time(),
        }
        profile["id"] = digest(profile)
        # Artifact stays immutable; calibrated coverage belongs to the frozen profile.
        with self.store.transaction() as db:
            count = db.execute(
                "UPDATE artifacts SET profile=? WHERE id=? AND profile IS NULL AND status='SHADOW'",
                (canonical(profile), artifact["id"]),
            ).rowcount
            if not count:
                raise ValueError("Candidate changed during calibration")
        return profile

    def evaluate(self, site, *, verifier):
        site = self._resolve(site)
        artifact = self._artifact(site.version)
        if artifact is None or artifact["status"] != "SHADOW" or not artifact["profile"]:
            raise ValueError("Evaluation requires a calibrated shadow candidate")
        profile = artifact["profile"]
        requirements = PromotionRequirements(**profile["requirements"])
        payload = dict(artifact["payload"], coverage=profile["coverage"])
        history = self.store.history(site.version)
        shadow = [
            r
            for r in history
            if r["artifact"] == artifact["id"]
            and r["created"] > artifact["epoch"]
            and r["prediction"] is not None
            and r["source"] == "fallback"
            and r["outcome"] is not None
        ]
        used_tasks = {
            r["task"] for r in history if r["id"] in set(sum(payload["partitions"].values(), []))
        }
        shadow = [r for r in shadow if r["task"] not in used_tasks]
        if len({r["task"] for r in shadow}) < requirements.min_samples:
            raise ValueError("Insufficient fresh shadow tasks with outcomes")
        ids = set(payload["partitions"]["evaluation"])
        holdout = [r for r in history if r["id"] in ids]
        engine = self.engines[payload["engine_data"]["engine"]]
        held_records = verify_rows(holdout, engine, payload, verifier)
        shadow_records = verify_rows(shadow, engine, payload, verifier)
        held_stats, shadow_stats = statistics(held_records), statistics(shadow_records)
        region_stats = {}
        for region in profile["coverage"]:
            region_stats[region] = {
                "holdout": statistics([r for r in held_records if r["region"] == region]),
                "shadow": statistics([r for r in shadow_records if r["region"] == region]),
            }
        expected_verifiers = {
            (r["candidate"]["verifier"], r["candidate"]["verifier_version"])
            for r in profile["calibration_evidence"]
        }
        current_verifiers = {
            (r["candidate"]["verifier"], r["candidate"]["verifier_version"])
            for r in held_records + shadow_records
        }
        if expected_verifiers != current_verifiers:
            raise ValueError("Verifier changed since calibration")
        qualified = (
            passes(held_stats, requirements)
            and passes(shadow_stats, requirements)
            and all(
                passes(stats, requirements)
                for region in region_stats.values()
                for stats in region.values()
            )
        )
        evidence = {
            "profile_id": profile["id"],
            "holdout": held_stats,
            "shadow": shadow_stats,
            "holdout_records": held_records,
            "shadow_records": shadow_records,
            "qualified": qualified,
            "regions": region_stats,
        }
        with self.store.transaction() as db:
            current = db.execute(
                "SELECT status,epoch FROM artifacts WHERE id=?", (artifact["id"],)
            ).fetchone()
            if current["status"] != "SHADOW" or current["epoch"] != artifact["epoch"]:
                raise ValueError("Candidate changed during evaluation")
            db.execute(
                "UPDATE artifacts SET evidence=? WHERE id=?", (canonical(evidence), artifact["id"])
            )
            if qualified:
                self._event(db, artifact["id"], "SHADOW", "VERIFIED", evidence)
                self._event(db, artifact["id"], "VERIFIED", "ACTIVE", {"profile": profile["id"]})
                db.execute(
                    "UPDATE artifacts SET status='ACTIVE',epoch=? WHERE id=?",
                    (time.time(), artifact["id"]),
                )
        return evidence

    def reevaluate(self, site):
        site = self._resolve(site)
        artifact = self._artifact(site.version)
        if artifact is None or artifact["status"] != "ACTIVE":
            return {"demoted": False, "reason": "no_active_path"}
        req = PromotionRequirements(**artifact["profile"]["requirements"])
        rows = [
            r
            for r in self.store.history(site.version)
            if r["artifact"] == artifact["id"] and r["created"] > artifact["epoch"]
        ][-req.evaluation_window :]
        active = [r for r in rows if r["source"] == "fast_path"]
        comparison = [r for r in rows if r["reason"] == "comparison"]
        factual = active + comparison
        missing = sum(r["outcome"] is None for r in factual)
        values = grouped_quality([r for r in active if r["outcome"] is not None])
        baseline = grouped_quality([r for r in comparison if r["outcome"] is not None])
        unsupported = sum(
            r["reason"]
            in ("outside_coverage", "insufficient_confidence", "engine_or_store_unavailable")
            for r in rows
        ) / max(len(rows), 1)
        enough = len(values) >= req.min_samples and len(baseline) >= req.min_samples
        identities = {
            (r["outcome"]["verifier"], r["outcome"]["verifier_version"])
            for r in factual
            if r["outcome"]
        }
        delta = (sum(values) / len(values) - sum(baseline) / len(baseline)) if enough else None
        delta_lower = (delta - math.sqrt(math.log(20) / (2 * len(values)))
                       - math.sqrt(math.log(20) / (2 * len(baseline)))) if enough else None
        demote = (
            (len(rows) >= req.evaluation_window and (missing > 0 or not enough))
            or (enough and (lower_bound(values) < req.min_quality
                           or delta_lower < -req.max_degradation))
            or (len(rows) >= req.min_samples and unsupported > req.max_uncovered_rate)
            or len(identities) > 1
        )
        evidence = {
            "demoted": demote,
            "active_samples": len(values),
            "comparison_samples": len(baseline),
            "missing_outcomes": missing,
            "quality_lower": lower_bound(values),
            "delta": delta,
            "delta_lower": delta_lower,
            "uncovered_rate": unsupported,
        }
        if demote:
            with self.store.transaction() as db:
                count = db.execute(
                    "UPDATE artifacts SET status='SHADOW',epoch=? "
                    "WHERE id=? AND status='ACTIVE' AND epoch=?",
                    (time.time(), artifact["id"], artifact["epoch"]),
                ).rowcount
                if count:
                    self._event(db, artifact["id"], "ACTIVE", "SHADOW", evidence)
        return evidence

    def maintenance(self, *, verifier=None, requirements=None, engine="exact"):
        results = {}
        for row in self.sites():
            site = DecisionSite(**row["contract"])
            try:
                artifact = self._artifact(site.version)
                if artifact is None:
                    results[site.name] = {"compiled": self.compile(site, engine=engine)}
                elif artifact["status"] == "ACTIVE":
                    results[site.name] = self.reevaluate(site)
                elif verifier is not None:
                    if artifact["profile"] is None:
                        if requirements is None:
                            continue
                        self.calibrate(site, verifier=verifier, requirements=requirements)
                    results[site.name] = self.evaluate(site, verifier=verifier)
            except (ValueError, KeyError) as error:
                results[site.name] = {"pending": str(error)}
        return results

    def sites(self):
        return [
            self.inspect(DecisionSite(**json.loads(r["contract"])))
            for r in self.store.rows("SELECT contract FROM sites ORDER BY name,created")
        ]

    def profile(self, site):
        site = self._resolve(site)
        return profile_history(self.store.history(site.version))

    def inspect(self, site):
        site = self._resolve(site)
        rows = self.store.history(site.version)
        try:
            artifact = self._artifact(site.version)
            error = None
        except (ValueError, KeyError) as exc:
            artifact, error = None, str(exc)
        fast = [r for r in rows if r["source"] == "fast_path"]
        verified = [r for r in fast if r["outcome"] and r["outcome"]["quality"] == 1]
        profiler = profile_history(rows)
        usage = {}
        for key in ("model_calls", "input_tokens", "output_tokens", "cost"):
            known = [r["usage"][key] for r in rows if r["usage"].get(key) is not None]
            usage[key] = sum(known) if known else None
        active = [r["outcome"]["quality"] for r in fast if r["outcome"]]
        comparison = [
            r["outcome"]["quality"] for r in rows if r["reason"] == "comparison" and r["outcome"]
        ]
        return {
            "name": site.name,
            "version": site.version,
            "contract": asdict(site),
            "state": artifact["status"] if artifact else "OBSERVE",
            "error": error,
            "fast_path": artifact["id"] if artifact else None,
            "observations": len(rows),
            "unique_states": len({canonical(r["state"]) for r in rows}),
            "choices": dict(Counter(r["choice"] for r in rows)),
            "coverage": len(fast) / len(rows) if rows else 0,
            "fallbacks": len(rows) - len(fast),
            "fallbacks_avoided": len(fast),
            "verified_fast_path_decisions": len(verified),
            "model_calls_avoided": None,  # Fallback invocations need not equal model calls.
            "outcome_completeness": sum(r["outcome"] is not None for r in rows) / len(rows)
            if rows
            else 0,
            "outcome_delta": sum(active) / len(active) - sum(comparison) / len(comparison)
            if active and comparison
            else None,
            "fallback_reasons": dict(Counter(r["reason"] for r in rows if r["reason"])),
            "usage": usage,
            "profiler": profiler,
            "profile": artifact["profile"] if artifact else None,
            "evidence": json.loads(artifact["evidence"])
            if artifact and artifact["evidence"]
            else None,
            "lifecycle": self.store.rows(
                "SELECT * FROM events WHERE artifact=? ORDER BY id", (artifact["id"],)
            )
            if artifact
            else [],
        }


_default_client = None


def decision(*, site, state, choices=None, fallback, **kwargs):
    global _default_client
    if _default_client is None:
        _default_client = Microloop()
    return _default_client.decide(
        site=site, state=state, choices=choices, fallback=fallback, **kwargs
    )


def record_outcome(decision_id, **kwargs):
    if _default_client is None:
        raise ValueError("No default client has recorded a decision")
    return _default_client.record_outcome(decision_id, **kwargs)
