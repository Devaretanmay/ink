"""Explicit bounded decisions and evidence-driven local fast paths."""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import secrets
import sqlite3
import time
import uuid
from collections import Counter
from dataclasses import asdict

import numpy as np

from .internal.contracts import (
    DecisionResult,
    DecisionSite,
    FallbackResult,
    Outcome,
    PromotionRequirements,
    canonical,
    digest,
)
from .internal.coverage import (
    CoverageEngine,
    RegionHealth,
    TextVectorizer,
    _extract_text,
    calibrate_semantic_boundaries,
)
from .internal.decision_store import DecisionStore, split_history
from .internal.engines import DecisionModelEngine, ExactEngine, resolve_engine_key
from .internal.profiler import profile_history
from .internal.verification import grouped_quality, lower_bound, passes, statistics, verify_rows


class Microloop:
    def __init__(self, path=".microloop/decisions.db", *, engines=(), readonly=False):
        self.store = DecisionStore(path, readonly=readonly)
        integral = {e.name: e for e in (ExactEngine(), DecisionModelEngine(), *engines)}
        # Historical artifacts carry engine="laya"; resolve them to the integral engine.
        for alias, current in (("laya", "decision"), ("microloop-decision-v1", "decision")):
            if alias not in integral and current in integral:
                integral[alias] = integral[current]
        self.engines = integral
        self._contracts = {}

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def register(self, site: DecisionSite):
        contract = canonical(asdict(site))
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO sites VALUES (?,?,?,?)",
                (site.version, site.name, contract, time.time()),
            )
        self._contracts[site.name] = site
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
        try:
            existing = self.store.rows(
                "SELECT contract FROM sites WHERE name=? ORDER BY created DESC", (site,)
            )
        except sqlite3.Error:
            known = self._contracts.get(site)
            if known is None:
                raise ValueError(
                    "Storage unavailable; pass an explicit DecisionSite contract"
                ) from None
            existing = [{"contract": canonical(asdict(known))}]
        if existing:
            contract = json.loads(existing[0]["contract"])
            if choices is not None:
                contract["choices"] = choices
            if fallback_revision is not None:
                contract["fallback_revision"] = fallback_revision
            return self._site(DecisionSite(**contract), state, None, None)
        if choices is None:
            raise ValueError("A new site requires choices or explicit registration")
        types = {str: "string", int: "integer", float: "number", bool: "boolean"}
        try:
            schema = {key: types[type(value)] for key, value in state.items()}
        except KeyError as error:
            raise ValueError(
                "Use DecisionSite with an explicit schema for optional fields"
            ) from error
        return self._site(
            DecisionSite(site, schema, tuple(choices), fallback_revision or "1"), state, None, None
        )

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

    def _effective_comparison_rate(self, artifact, profile) -> float:
        req = profile.get("requirements", {})
        base_rate = req.get("comparison_rate", 0.25)
        if not req.get("allow_adaptive_comparison", True) or req.get("high_risk", False):
            return base_rate
        min_floor = req.get("min_comparison_rate", 0.05)
        if artifact and artifact.get("id"):
            latest_drift = self.store.rows(
                "SELECT demoted, delta_lower, missing_outcomes FROM drift_checks "
                "WHERE artifact=? ORDER BY created DESC, id DESC LIMIT 1",
                (artifact["id"],),
            )
            if latest_drift:
                d = latest_drift[0]
                if d.get("demoted") or (d.get("delta_lower") is not None and d["delta_lower"] < 0):
                    return min(0.50, max(base_rate * 1.5, 0.25))
                if d.get("missing_outcomes", 0) > 0:
                    return min(0.50, max(base_rate * 1.5, 0.25))

            cov_row = self.store.rows(
                "SELECT SUM(fast_served) as fast_count FROM state_coverage WHERE site=?",
                (artifact["site"],),
            )
            verified_count = (
                cov_row[0]["fast_count"] if cov_row and cov_row[0]["fast_count"] else 0
            )
            min_samples = req.get("min_samples", 10)
            if verified_count >= 2 * min_samples:
                scale = math.sqrt((2 * min_samples) / verified_count)
                return max(min_floor, round(base_rate * scale, 4))
        return base_rate

    def _route(self, site, state):
        artifact = None
        try:
            artifact = self._artifact(site.version)
            if artifact is None:
                return None, None, "observe"
            payload = artifact["payload"]
            profile = artifact["profile"]
            coverage = profile["coverage"] if profile else payload["coverage"]

            region = None
            is_semantic_shadow = False
            level = None

            if profile and "coverage_engine" in profile:
                cov_engine = CoverageEngine.from_dict(profile["coverage_engine"])
                active_sem = set()
                if artifact.get("evidence"):
                    try:
                        ev_raw = artifact["evidence"]
                        ev = json.loads(ev_raw) if isinstance(ev_raw, str) else ev_raw
                        active_sem = set(ev.get("qualified_semantic_regions", []))
                    except Exception:
                        pass
                if artifact["status"] == "ACTIVE" and active_sem:
                    for r in cov_engine.semantic_regions:
                        if r.region_id in active_sem:
                            r.status = "ACTIVE"
                level, reg, conf = cov_engine.route(state)
                if level == "exact":
                    region = reg
                elif level == "semantic":
                    region = reg
                elif level == "shadow":
                    region = reg
                    is_semantic_shadow = True
                else:
                    return artifact, None, "outside_coverage"
            else:
                region = coverage.get(canonical(state))
                if region is None:
                    return artifact, None, "outside_coverage"

            engine = self.engines[resolve_engine_key(payload["engine_data"]["engine"])]
            state_in = state
            has_proto = region and "prototype_state" in region
            if (is_semantic_shadow or level == "semantic") and has_proto:
                if (
                    payload["engine_data"].get("engine") == "exact"
                    and canonical(state) not in payload["engine_data"].get("table", {})
                ):
                    state_in = region["prototype_state"]
            choice, probability = engine.predict(payload["engine_data"], state_in)
            if (
                choice not in site.choices
                or not math.isfinite(probability)
                or not 0 <= probability <= 1
            ):
                raise ValueError("Invalid prediction")
            rep_version = (
                "sparse_tfidf_v1"
                if level in ("semantic", "shadow")
                else ("exact_v1" if level == "exact" else "none")
            )
            route_info = {
                "semantic_region": region.get("region_id") if isinstance(region, dict) else None,
                "representation_version": rep_version,
                "distance": region.get("distance") if isinstance(region, dict) else None,
                "radius": region.get("radius") if isinstance(region, dict) else None,
                "negative_margin": (
                    region.get("negative_margin") if isinstance(region, dict) else None
                ),
                "route_level": level or "exact",
            }
            prediction = {
                "choice": choice,
                "raw_probability": probability,
                "confidence": region.get("confidence", 0.0),
                "route_info": route_info,
            }
            if artifact["status"] != "ACTIVE" or profile is None or is_semantic_shadow:
                return artifact, prediction, "shadow"
            if (
                choice != region.get("choice")
                or prediction["confidence"] < profile["requirements"]["min_confidence"]
            ):
                return artifact, prediction, "insufficient_confidence"
            eff_rate = self._effective_comparison_rate(artifact, profile)
            route_info["comparison_rate"] = eff_rate
            if secrets.randbelow(10**9) / 10**9 < eff_rate:
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
        if (
            source == "fallback"
            and site.fallback_model_calls is not None
            and fallback.model_calls != site.fallback_model_calls
        ):
            raise ValueError("Fallback usage violates the site's fixed model-call contract")
        route_info = prediction.get("route_info", {}) if prediction else {}
        receipt = {
            "site": site.name,
            "site_version": site.version,
            "artifact": artifact["id"] if artifact else None,
            "semantic_region": route_info.get("semantic_region"),
            "representation_version": route_info.get("representation_version", "none"),
            "distance": route_info.get("distance"),
            "radius": route_info.get("radius"),
            "negative_margin": route_info.get("negative_margin"),
            "comparison_rate": route_info.get("comparison_rate"),
            "served_by": source,
            "verification_status": "verified"
            if source == "fast_path"
            else ("shadow" if reason in ("shadow", "comparison") else "fallback"),
        }
        if prediction is not None:
            prediction["receipt"] = receipt
        result = DecisionResult(
            fallback.choice,
            uuid.uuid4().hex,
            source,
            site.version,
            artifact["id"] if artifact else None,
            reason,
            prediction["confidence"] if prediction else None,
            receipt=receipt,
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
            db.execute(
                """INSERT INTO state_coverage(site, state, observations, fast_served)
                VALUES (?,?,1,?) ON CONFLICT(site, state) DO UPDATE SET
                observations=observations+1, fast_served=fast_served+excluded.fast_served""",
                (site.version, canonical(state), 1 if source == "fast_path" else 0),
            )
        return result

    def _save_fallback(self, site, state, value, task_id, started, artifact, prediction, reason):
        try:
            return self._save(site, state, value, task_id, started, artifact, prediction, reason)
        except sqlite3.Error:
            choice = value.choice if isinstance(value, FallbackResult) else value
            if choice not in site.choices:
                raise ValueError("Fallback returned an undeclared choice") from None
            receipt = {
                "site": site.name,
                "site_version": site.version,
                "artifact": artifact["id"] if artifact else None,
                "semantic_region": None,
                "representation_version": "none",
                "distance": None,
                "radius": None,
                "negative_margin": None,
                "comparison_rate": None,
                "served_by": "fallback",
                "verification_status": "fallback",
            }
            return DecisionResult(
                choice,
                uuid.uuid4().hex,
                "fallback",
                site.version,
                fallback_reason="storage_unavailable",
                recorded=False,
                receipt=receipt,
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
        artifact, prediction, reason = await asyncio.to_thread(self._route, site, state)
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
                row = db.execute(
                    "SELECT site, state FROM decisions WHERE id=?", (decision_id,)
                ).fetchone()
                db.execute(
                    """INSERT INTO state_coverage(site, state, observations, outcomes, quality_sum)
                    VALUES (?,?,0,1,?) ON CONFLICT(site, state) DO UPDATE SET
                    outcomes=outcomes+1, quality_sum=quality_sum+excluded.quality_sum""",
                    (row["site"], row["state"], outcome.quality),
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

    def compile(self, site, *, engine="decision", replace_existing=False):
        site = self._resolve(site)
        engine = resolve_engine_key(engine)
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
                self._event(
                    db, previous["id"], previous["status"], "RETIRED", {"replaced_by": artifact_id}
                )
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
            if previous:
                db.execute(
                    "INSERT OR IGNORE INTO artifact_links VALUES (?,?,?)",
                    (previous["id"], artifact_id, time.time()),
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
            rows,
            self.engines[resolve_engine_key(payload["engine_data"]["engine"])],
            payload,
            verifier,
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
        texts = [_extract_text(r["state"]) for r in records]
        vectorizer = TextVectorizer.fit(texts)
        semantic_regions = calibrate_semantic_boundaries(
            records,
            vectorizer,
            site.version,
            min_region_samples=requirements.min_region_samples,
        )
        cov_engine = CoverageEngine(
            exact_coverage=coverage,
            semantic_regions=semantic_regions,
            vectorizer=vectorizer,
        )
        profile = {
            "requirements": asdict(requirements),
            "coverage": coverage,
            "coverage_engine": cov_engine.to_dict(),
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

    def evaluate(self, site, *, verifier, auto_promote: bool = True):
        site = self._resolve(site)
        artifact = self._artifact(site.version)
        if artifact is None or artifact["status"] != "SHADOW" or not artifact["profile"]:
            raise ValueError("Evaluation requires a calibrated shadow candidate")
        profile = artifact["profile"]
        requirements = PromotionRequirements(**profile["requirements"])
        payload = dict(
            artifact["payload"],
            coverage=profile["coverage"],
            coverage_engine=profile.get("coverage_engine"),
        )
        history = self.store.history(site.version)
        shadow = [
            r
            for r in history
            if r["artifact"] == artifact["id"]
            and r["created"] > artifact["epoch"]
            and r["prediction"] is not None
            and r["source"] == "fallback"
        ]
        used_tasks = {
            r["task"] for r in history if r["id"] in set(sum(payload["partitions"].values(), []))
        }
        shadow = [r for r in shadow if r["task"] not in used_tasks]
        if any(
            r["outcome"] is None for r in shadow if canonical(r["state"]) in profile["coverage"]
        ):
            raise ValueError("Missing fresh shadow outcomes; qualification would be biased")
        if len({r["task"] for r in shadow}) < requirements.min_samples:
            raise ValueError("Insufficient fresh shadow tasks with outcomes")
        ids = set(payload["partitions"]["evaluation"])
        holdout = [r for r in history if r["id"] in ids]
        engine = self.engines[resolve_engine_key(payload["engine_data"]["engine"])]
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
        qualified_sem_regions = []
        sem_region_stats = {}
        if profile.get("coverage_engine"):
            cov_engine = CoverageEngine.from_dict(profile["coverage_engine"])
            for sem_reg in cov_engine.semantic_regions:
                reg_shadow = [r for r in shadow_records if r["region"] == sem_reg.region_id]
                reg_held = [r for r in held_records if r["region"] == sem_reg.region_id]
                sem_records = reg_shadow + reg_held
                if sem_records:
                    sem_stats = statistics(sem_records)
                    sem_region_stats[sem_reg.region_id] = sem_stats
                    if (
                        sem_stats["samples"] >= requirements.min_region_samples
                        and sem_stats["quality_lower"] >= requirements.min_confidence
                        and passes(sem_stats, requirements)
                        and all(r["choice"] == sem_reg.choice for r in sem_records)
                    ):
                        qualified_sem_regions.append(sem_reg.region_id)
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
            "qualified_semantic_regions": qualified_sem_regions,
            "semantic_region_stats": sem_region_stats,
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
            db.execute(
                "INSERT OR REPLACE INTO promotion_records VALUES (?,?,?,?,?,?,?,?)",
                (
                    artifact["id"],
                    time.time(),
                    int(qualified),
                    held_stats.get("samples", 0),
                    shadow_stats.get("samples", 0),
                    held_stats.get("quality_lower"),
                    shadow_stats.get("delta_lower"),
                    held_stats.get("agreement"),
                ),
            )
            if qualified:
                self._event(db, artifact["id"], "SHADOW", "VERIFIED", evidence)
                if auto_promote:
                    self._event(
                        db, artifact["id"], "VERIFIED", "ACTIVE", {"profile": profile["id"]}
                    )
                    db.execute(
                        "UPDATE artifacts SET status='ACTIVE',epoch=? WHERE id=?",
                        (time.time(), artifact["id"]),
                    )
                else:
                    db.execute(
                        "UPDATE artifacts SET status='VERIFIED' WHERE id=?",
                        (artifact["id"],),
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
        expected_identities = {
            (r["candidate"]["verifier"], r["candidate"]["verifier_version"])
            for r in artifact["profile"]["calibration_evidence"]
        }
        delta = (sum(values) / len(values) - sum(baseline) / len(baseline)) if enough else None
        delta_lower = (
            (
                delta
                - math.sqrt(math.log(20) / (2 * len(values)))
                - math.sqrt(math.log(20) / (2 * len(baseline)))
            )
            if enough
            else None
        )
        demote = (
            (len(rows) >= req.evaluation_window and (missing > 0 or not enough))
            or (
                enough
                and (lower_bound(values) < req.min_quality or delta_lower < -req.max_degradation)
            )
            or (len(rows) >= req.min_samples and unsupported > req.max_uncovered_rate)
            or bool(identities - expected_identities)
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
        with self.store.transaction() as db:
            db.execute(
                """INSERT INTO drift_checks(artifact, created, demoted, active_samples,
                comparison_samples, missing_outcomes, quality_lower, delta_lower, uncovered_rate)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    artifact["id"],
                    time.time(),
                    int(demote),
                    evidence["active_samples"],
                    evidence["comparison_samples"],
                    evidence["missing_outcomes"],
                    evidence["quality_lower"],
                    evidence["delta_lower"],
                    evidence["uncovered_rate"],
                ),
            )
        return evidence

    def maintenance(
        self,
        sites: list[str | DecisionSite] | None = None,
        *,
        max_sites: int | None = None,
        max_rows: int | None = None,
        time_budget_sec: float | None = None,
        verifier=None,
        requirements=None,
        engine="decision",
    ):
        results = {}
        engine = resolve_engine_key(engine)
        if sites is not None:
            resolved_sites = [self._resolve(s) for s in sites]
        else:
            resolved_sites = [
                DecisionSite(**json.loads(r["contract"]))
                for r in self.store.rows("SELECT contract FROM sites ORDER BY name,created")
            ]

        def site_priority_key(site: DecisionSite):
            art = self._artifact(site.version)
            st = art["status"] if art else "OBSERVE"
            drift_checks = self.drift_history(site)
            last_drift = drift_checks[0]["created"] if drift_checks else 0.0
            hist = self.store.history(site.version)
            obs_count = len(hist)
            if st == "ACTIVE":
                return (0, last_drift, -obs_count, site.name)
            elif st == "SHADOW":
                shadow_outcomes = sum(
                    1
                    for r in hist
                    if art
                    and r["created"] > art["epoch"]
                    and r["prediction"] is not None
                    and r["outcome"] is not None
                )
                return (1, -shadow_outcomes, site.name)
            elif st == "CANDIDATE":
                return (2, -obs_count, site.name)
            elif st == "OBSERVE":
                return (3, -obs_count, site.name)
            else:
                return (4, 0, site.name)

        prioritized = sorted(resolved_sites, key=site_priority_key)
        start_time = time.perf_counter()
        total_rows = 0

        for site in prioritized:
            if max_sites is not None and len(results) >= max_sites:
                results[site.name] = {"deferred": "max_sites_reached"}
                continue
            if (
                time_budget_sec is not None
                and (time.perf_counter() - start_time) >= time_budget_sec
            ):
                results[site.name] = {"deferred": "time_budget_exceeded"}
                continue
            if max_rows is not None and total_rows >= max_rows:
                results[site.name] = {"deferred": "max_rows_reached"}
                continue

            site_rows = len(self.store.history(site.version))
            total_rows += site_rows
            try:
                artifact = self._artifact(site.version)
                if artifact is None:
                    if requirements is None:
                        raise ValueError("Automatic compilation requires explicit requirements")
                    self._require_compilation_support(site, requirements)
                    results[site.name] = {"compiled": self.compile(site, engine=engine)}
                elif artifact["status"] == "ACTIVE":
                    reeval = self.reevaluate(site)
                    results[site.name] = reeval
                    cur = self._artifact(site.version)
                    if cur and cur["status"] == "ACTIVE" and cur.get("profile"):
                        prof = cur["profile"]
                        cov_data = prof.get("coverage_engine")
                        req_dict = prof.get("requirements", {})
                        if cov_data:
                            cov_engine = CoverageEngine.from_dict(cov_data)
                            history = self.store.history(site.version)
                            recent_counterexamples = [
                                r for r in history[-50:]
                                if r.get("outcome") and r["outcome"].get("quality", 1.0) < 0.5
                            ]
                            tightened_any = False
                            for ce in recent_counterexamples:
                                tightened = cov_engine.ingest_counterexample(
                                    ce["state"], ce["choice"]
                                )
                                if tightened:
                                    tightened_any = True
                                    with self.store.transaction() as db:
                                        self._event(
                                            db, cur["id"], "ACTIVE", "ACTIVE",
                                            {"event": "region_tightened", "regions": tightened},
                                        )

                            if (
                                req_dict.get("allow_region_split", True)
                                and not req_dict.get("high_risk", False)
                            ):
                                min_reg_s = req_dict.get("min_region_samples", 5)
                                split_candidates = []
                                for reg in list(cov_engine.semantic_regions):
                                    if reg.status == "ACTIVE" and "." not in reg.region_id:
                                        matching = [
                                            (
                                                cov_engine.vectorizer.transform(
                                                    _extract_text(r["state"])
                                                ),
                                                r["state"],
                                            )
                                            for r in history
                                            if reg.contains(
                                                cov_engine.vectorizer.transform(
                                                    _extract_text(r["state"])
                                                )
                                            )[0]
                                        ]
                                        if len(matching) >= 2 * min_reg_s:
                                            vecs = [m[0] for m in matching]
                                            sts = [m[1] for m in matching]
                                            split = reg.detect_multimodal_split(
                                                vecs, sts, min_reg_s
                                            )
                                            if split:
                                                split_candidates.extend(split)
                                                ev_data = {
                                                    "event": "region_split_candidate_created",
                                                    "parent_region": reg.region_id,
                                                    "child_a": split[0].region_id,
                                                    "child_b": split[1].region_id,
                                                }
                                                with self.store.transaction() as db:
                                                    self._event(
                                                        db, cur["id"], "ACTIVE", "ACTIVE", ev_data
                                                    )
                                for sc in split_candidates:
                                    cov_engine.semantic_regions.append(sc)

                            if tightened_any:
                                prof["coverage_engine"] = cov_engine.to_dict()
                                with self.store.transaction() as db:
                                    db.execute(
                                        "UPDATE artifacts SET profile=? WHERE id=?",
                                        (canonical(prof), cur["id"]),
                                    )
                                results[site.name]["self_tuned"] = True
                elif verifier is not None:
                    if artifact["profile"] is None:
                        if requirements is None:
                            continue
                        try:
                            self.calibrate(site, verifier=verifier, requirements=requirements)
                        except ValueError as error:
                            if (
                                str(error)
                                != "No state regions have sufficient calibration evidence"
                            ):
                                raise
                            self._require_compilation_support(site, requirements)
                            history = self.store.history(site.version)
                            new_rows = [
                                r
                                for r in history
                                if r["created"] > artifact["payload"]["created"]
                                and r["outcome"] is not None
                            ]
                            if len(new_rows) < requirements.min_samples:
                                raise
                            self.compile(site, engine=engine, replace_existing=True)
                            self.calibrate(site, verifier=verifier, requirements=requirements)
                    req_dict = (
                        artifact["profile"].get("requirements", {})
                        if artifact["profile"]
                        else {}
                    )
                    if artifact["profile"] is not None and (
                        req_dict.get("high_risk", False)
                        or not req_dict.get("allow_auto_requalify", True)
                    ):
                        results[site.name] = {"pending": "auto_requalify_disabled_for_site"}
                    else:
                        results[site.name] = self.evaluate(site, verifier=verifier)
            except (ValueError, KeyError, sqlite3.Error) as error:
                results[site.name] = {"pending": str(error)}
        return results

    def _require_compilation_support(self, site, requirements):
        rows = [
            r
            for r in self.store.history(site.version)
            if r["source"] == "fallback" and r["outcome"] is not None
        ]
        train, calibration, evaluation = split_history(rows)
        if requirements.min_confidence == 1 or requirements.max_degradation == 0:
            raise ValueError("Finite samples cannot meet the configured confidence bounds")
        calibration_min = max(
            requirements.min_region_samples,
            math.ceil(math.log(20) / (2 * (1 - requirements.min_confidence) ** 2)),
        )
        evaluation_min = max(
            requirements.min_samples, math.ceil(2 * math.log(20) / requirements.max_degradation**2)
        )
        trained = {canonical(r["state"]) for r in train}
        cal, held = {}, {}
        for part, counts in ((calibration, cal), (evaluation, held)):
            for record in part:
                counts.setdefault(canonical(record["state"]), set()).add(record["task"])
        if not any(
            len(cal.get(region, ())) >= calibration_min
            and len(held.get(region, ())) >= evaluation_min
            for region in trained
        ):
            raise ValueError("Insufficient independent calibration/evaluation tasks to compile")

    def sites(self):
        return [
            self.inspect(DecisionSite(**json.loads(r["contract"])))
            for r in self.store.rows("SELECT contract FROM sites ORDER BY name,created")
        ]

    def profile(self, site):
        site = self._resolve(site)
        return profile_history(self.store.history(site.version))

    def coverage(self, site):
        """Queryable per-state counters: observations, fast serves, outcomes, quality."""
        site = self._resolve(site)
        return self.store.rows(
            "SELECT state, observations, fast_served, outcomes, quality_sum "
            "FROM state_coverage WHERE site=? ORDER BY observations DESC",
            (site.version,),
        )

    def promotions(self, site):
        """Queryable promotion decisions per artifact, newest first."""
        site = self._resolve(site)
        return self.store.rows(
            "SELECT p.* FROM promotion_records p JOIN artifacts a ON a.id=p.artifact "
            "WHERE a.site=? ORDER BY p.decided DESC",
            (site.version,),
        )

    def drift_history(self, site):
        """Queryable re-evaluation ticks per artifact, newest first."""
        site = self._resolve(site)
        return self.store.rows(
            "SELECT d.* FROM drift_checks d JOIN artifacts a ON a.id=d.artifact "
            "WHERE a.site=? ORDER BY d.created DESC, d.id DESC",
            (site.version,),
        )

    def lineage(self, site):
        """Artifact parent-to-child links from recompilation replacements."""
        site = self._resolve(site)
        return self.store.rows(
            "SELECT l.* FROM artifact_links l JOIN artifacts a ON a.id=l.child "
            "WHERE a.site=? ORDER BY l.created",
            (site.version,),
        )

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
        for key in ("model_calls", "input_tokens", "output_tokens", "cost", "request_attempts"):
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
            "model_calls_avoided": len(fast) * site.fallback_model_calls
            if site.fallback_model_calls is not None
            else None,
            "verified_model_calls_avoided": len(verified) * site.fallback_model_calls
            if site.fallback_model_calls is not None
            else None,
            "savings_basis": "declared_and_validated_fixed_call_count"
            if site.fallback_model_calls is not None
            else "unknown",
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

    def health(self, site: str | DecisionSite) -> dict:
        site = self._resolve(site)
        insp = self.inspect(site)
        status = insp["state"]
        drift_rows = self.drift_history(site)
        drift_status = "none"
        if status == "ACTIVE":
            drift_status = "clean"
            if drift_rows and drift_rows[0].get("demoted"):
                drift_status = "demoted"
        elif status == "SHADOW" and drift_rows and any(r.get("demoted") for r in drift_rows):
            status = "DEMOTED"
            drift_status = "demoted"

        last_maint = None
        if insp.get("lifecycle"):
            last_maint = insp["lifecycle"][-1]["created"]

        rows = self.store.history(site.version)
        fast_rows = [r for r in rows if r["source"] == "fast_path"]
        false_serves = sum(
            1 for r in fast_rows if r["outcome"] and r["outcome"].get("quality", 1.0) < 1.0
        )

        return {
            "name": site.name,
            "version": site.version,
            "status": status,
            "observations": insp["observations"],
            "fast_served": insp["fallbacks_avoided"],
            "coverage": insp["coverage"],
            "false_serves": false_serves,
            "drift_status": drift_status,
            "last_maintenance": last_maint,
            "savings": {
                "model_calls_avoided": insp["model_calls_avoided"],
                "verified_model_calls_avoided": insp["verified_model_calls_avoided"],
                "savings_basis": insp["savings_basis"],
            },
        }

    def fleet_health(self) -> dict:
        site_healths = {}
        active_count = 0
        shadow_count = 0
        observe_count = 0
        demoted_count = 0
        total_obs = 0
        total_fast = 0
        total_false = 0

        for row in self.store.rows("SELECT contract FROM sites ORDER BY name,created"):
            site = DecisionSite(**json.loads(row["contract"]))
            h = self.health(site)
            site_healths[site.name] = h
            st = h["status"]
            if st == "ACTIVE":
                active_count += 1
            elif st == "SHADOW":
                shadow_count += 1
            elif st == "OBSERVE":
                observe_count += 1
            elif st == "DEMOTED":
                demoted_count += 1

            total_obs += h["observations"]
            total_fast += h["fast_served"]
            total_false += h["false_serves"]

        return {
            "fleet_size": len(site_healths),
            "sites": site_healths,
            "summary": {
                "active_sites": active_count,
                "shadow_sites": shadow_count,
                "observe_sites": observe_count,
                "demoted_sites": demoted_count,
                "total_observations": total_obs,
                "total_fast_served": total_fast,
                "fleet_coverage": (total_fast / total_obs) if total_obs > 0 else 0.0,
                "total_false_serves": total_false,
            },
        }

    def receipt(self, decision_id: str) -> dict:
        rows = self.store.rows("SELECT * FROM decisions WHERE id=?", (decision_id,))
        if not rows:
            raise KeyError(f"Unknown decision ID: {decision_id}")
        row = rows[0]
        pred = json.loads(row["prediction"]) if row.get("prediction") else {}
        if "receipt" in pred:
            return pred["receipt"]
        source = row["source"]
        reason = row["reason"]
        route_info = pred.get("route_info", {})
        return {
            "site": row["site"],
            "site_version": row["site"],
            "artifact": row["artifact"],
            "semantic_region": route_info.get("semantic_region"),
            "representation_version": route_info.get("representation_version", "none"),
            "distance": route_info.get("distance"),
            "radius": route_info.get("radius"),
            "negative_margin": route_info.get("negative_margin"),
            "comparison_rate": route_info.get("comparison_rate"),
            "served_by": source,
            "verification_status": "verified"
            if source == "fast_path"
            else ("shadow" if reason in ("shadow", "comparison") else "fallback"),
        }

    def hot_swap(self, site, new_artifact_id: str):
        site = self._resolve(site)
        with self.store.transaction() as db:
            old_art = db.execute(
                "SELECT id, status, epoch FROM artifacts WHERE site=? AND status='ACTIVE'",
                (site.version,),
            ).fetchone()
            new_art = db.execute(
                "SELECT id, status FROM artifacts WHERE id=?", (new_artifact_id,)
            ).fetchone()
            if not new_art or new_art["status"] not in ("VERIFIED", "SHADOW"):
                raise ValueError("Replacement artifact must be in VERIFIED or SHADOW state")
            now = time.time()
            if old_art:
                db.execute("UPDATE artifacts SET status='RETIRED' WHERE id=?", (old_art["id"],))
                self._event(
                    db, old_art["id"], "ACTIVE", "RETIRED", {"replaced_by": new_artifact_id}
                )
            db.execute(
                "UPDATE artifacts SET status='ACTIVE', epoch=? WHERE id=?",
                (now, new_artifact_id),
            )
            self._event(
                db,
                new_artifact_id,
                new_art["status"],
                "ACTIVE",
                {"replaces": old_art["id"] if old_art else None, "hot_swapped": True},
            )
            if old_art:
                db.execute(
                    "INSERT OR IGNORE INTO artifact_links VALUES (?,?,?)",
                    (old_art["id"], new_artifact_id, now),
                )
        return new_artifact_id

    def compact(
        self,
        site,
        *,
        keep_recent: int = 1000,
        before_timestamp: float | None = None,
        vacuum: bool = False,
    ) -> dict:
        site = self._resolve(site)
        with self.store.transaction() as db:
            active = db.execute(
                "SELECT id, epoch, payload FROM artifacts WHERE site=? AND status='ACTIVE'",
                (site.version,),
            ).fetchone()
            shadows = db.execute(
                "SELECT id, epoch, payload FROM artifacts "
                "WHERE site=? AND status IN ('SHADOW','CANDIDATE')",
                (site.version,),
            ).fetchall()

            protected_ids = set()
            for art in ([active] if active else []) + (shadows or []):
                try:
                    payload = json.loads(art["payload"])
                    for part_ids in payload.get("partitions", {}).values():
                        protected_ids.update(part_ids)
                except Exception:
                    pass

            min_epoch = active["epoch"] if active else None
            for s in shadows:
                if min_epoch is None or s["epoch"] < min_epoch:
                    min_epoch = s["epoch"]

            recent_ids = {
                r[0]
                for r in db.execute(
                    "SELECT id FROM decisions WHERE site=? ORDER BY created DESC LIMIT ?",
                    (site.version, keep_recent),
                ).fetchall()
            }
            protected_ids.update(recent_ids)
            now_t = time.time()
            cutoff = before_timestamp if before_timestamp is not None else (min_epoch or now_t)

            rows_to_prune = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM decisions WHERE site=? AND created < ?",
                    (site.version, cutoff),
                ).fetchall()
                if r[0] not in protected_ids
            ]

            if rows_to_prune:
                chunk_size = 500
                for i in range(0, len(rows_to_prune), chunk_size):
                    chunk = rows_to_prune[i : i + chunk_size]
                    q = f"DELETE FROM decisions WHERE id IN ({','.join('?' for _ in chunk)})"
                    db.execute(q, chunk)

            remaining = db.execute(
                "SELECT COUNT(*) FROM decisions WHERE site=?", (site.version,)
            ).fetchone()[0]

            self.store._rebuild_coverage(db, site.version)
            if active:
                self._event(
                    db,
                    active["id"],
                    "ACTIVE",
                    "ACTIVE",
                    {"event": "compaction", "pruned": len(rows_to_prune), "remaining": remaining},
                )

        if vacuum:
            with self.store.lock:
                self.store.conn.execute("VACUUM")

        return {
            "site": site.name,
            "version": site.version,
            "pruned": len(rows_to_prune),
            "remaining": remaining,
        }

    def region_health(self, site, region_id: str) -> dict:
        site = self._resolve(site)
        artifact = self._artifact(site.version)
        if not artifact or not artifact.get("profile"):
            raise KeyError(f"No calibrated artifact found for site {site.name}")
        cov_data = artifact["profile"].get("coverage_engine")
        if not cov_data:
            raise KeyError(f"No semantic coverage engine for site {site.name}")
        cov_engine = CoverageEngine.from_dict(cov_data)
        reg = cov_engine.get_region(region_id)
        if not reg:
            raise KeyError(f"Region {region_id} not found in coverage engine")

        rows = self.store.history(site.version)
        matched_rows, dists = [], []
        for r in rows:
            vec = cov_engine.vectorizer.transform(_extract_text(r["state"]))
            inside, dist = reg.contains(vec)
            if inside:
                matched_rows.append(r)
                dists.append(dist)

        sample_count = len(matched_rows)
        outcomes = [r["outcome"]["quality"] for r in matched_rows if r["outcome"] is not None]
        verified_quality = sum(outcomes) / len(outcomes) if outcomes else 0.0
        quality_lower = lower_bound(outcomes) if outcomes else 0.0

        comparisons = [r for r in matched_rows if r.get("reason") == "comparison"]
        disagreements = [
            r for r in comparisons if r.get("outcome") and r["choice"] != reg.choice
        ]
        disagreement_rate = (
            len(disagreements) / len(comparisons) if comparisons else 0.0
        )

        dist_mean = float(np.mean(dists)) if dists else 0.0
        dist_p95 = float(np.percentile(dists, 95)) if dists else 0.0

        latest_drift = self.drift_history(site)
        last_drift_time = latest_drift[0]["created"] if latest_drift else None

        health = RegionHealth(
            region_id=reg.region_id,
            status=reg.status,
            sample_count=sample_count,
            verified_quality=round(verified_quality, 4),
            quality_lower_bound=round(quality_lower, 4),
            comparison_disagreement_rate=round(disagreement_rate, 4),
            distance_mean=round(dist_mean, 4),
            distance_p95=round(dist_p95, 4),
            negative_margin=reg.negative_margin,
            radius=reg.radius,
            counterexample_count=reg.counterexample_count,
            outcome_completeness=round(len(outcomes) / max(sample_count, 1), 4),
            last_drift_check=last_drift_time,
            last_changed=reg.last_changed,
        )
        return health.to_dict()

    def all_region_health(self, site) -> list[dict]:
        site = self._resolve(site)
        artifact = self._artifact(site.version)
        if not artifact or not artifact.get("profile"):
            return []
        cov_data = artifact["profile"].get("coverage_engine")
        if not cov_data:
            return []
        cov_engine = CoverageEngine.from_dict(cov_data)
        return [
            self.region_health(site, reg.region_id)
            for reg in cov_engine.semantic_regions
        ]


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
