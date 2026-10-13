"""Statistical calibration, qualification verification, and drift reevaluation."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from ..internal.contracts import (
    DecisionSite,
    PromotionRequirements,
    canonical,
    digest,
)
from ..internal.coverage import (
    CoverageEngine,
    SemanticRegion,
    TextVectorizer,
    _extract_text,
    calibrate_semantic_boundaries,
)
from ..internal.engines import resolve_engine_key
from ..internal.verification import (
    grouped_quality,
    lower_bound,
    passes,
    statistics,
    verify_rows,
)
from .compiler import DEFAULT_REQUIREMENTS, compute_evidence_identity

if TYPE_CHECKING:
    from ..client import Ink

logger = logging.getLogger("ink.runtime.qualification")


class QualificationOrchestrator:
    """Orchestrates candidate calibration, empirical qualification, and drift detection."""

    def __init__(self, client: Ink) -> None:
        self.client = client

    @property
    def store(self):
        return self.client.store

    @property
    def engines(self):
        return self.client.engines

    def calibrate(
        self,
        site: str | DecisionSite,
        *,
        verifier=None,
        requirements: PromotionRequirements | None = None,
    ) -> dict[str, Any]:
        """Calibrates shadow candidate regions against verified outcomes from calibration partition."""
        resolved_site = self.client._resolve(site)
        reqs = requirements or self.client._maintenance_requirements or DEFAULT_REQUIREMENTS
        artifact = self.client._artifact(resolved_site.version)
        if artifact is None or artifact["status"] != "SHADOW" or artifact["profile"]:
            raise ValueError("Calibration requires an uncalibrated shadow candidate")
        payload = artifact["payload"]
        eng_key = resolve_engine_key(payload["engine_data"]["engine"])
        if verifier is None and eng_key != "exact":
            raise ValueError(
                f"Semantic engines require a verifier ({eng_key}). "
                "Pass a callable verifier to calibrate() or maintenance()."
            )
        ids = set(payload["partitions"]["calibration"])
        rows = [r for r in self.store.history(resolved_site.version) if r["id"] in ids]
        records = verify_rows(
            rows,
            self.engines[eng_key],
            payload,
            verifier,
        )
        regions: dict[str, list] = {}
        for record in records:
            regions.setdefault(record["region"], []).append(record)
        coverage: dict[str, dict] = {}
        for key, values in regions.items():
            stats = statistics(values)
            choices = {r["choice"] for r in values}
            if (
                stats["samples"] >= reqs.min_region_samples
                and len(choices) == 1
                and stats["quality_lower"] >= reqs.min_confidence
            ):
                coverage[key] = {
                    "confidence": stats["quality_lower"],
                    "choice": values[0]["choice"],
                    "samples": stats["samples"],
                }
        if not coverage:
            raise ValueError("No state regions have sufficient calibration evidence")

        cov_engine_candidate = None
        if payload.get("coverage_engine"):
            cov_engine_candidate = CoverageEngine.from_dict(payload["coverage_engine"])

        if cov_engine_candidate and cov_engine_candidate.semantic_regions:
            vectorizer = cov_engine_candidate.vectorizer
            semantic_regions = []
            for r in cov_engine_candidate.semantic_regions:
                r_copy = SemanticRegion.from_dict(r.to_dict())
                if r.region_id in coverage:
                    r_copy.confidence = coverage[r.region_id]["confidence"]
                semantic_regions.append(r_copy)
        else:
            texts = [_extract_text(r["state"]) for r in records]
            vectorizer = TextVectorizer.fit(texts)
            semantic_regions = calibrate_semantic_boundaries(
                records,
                vectorizer,
                resolved_site.version,
                min_region_samples=reqs.min_region_samples,
            )
        cov_engine = CoverageEngine(
            exact_coverage=coverage,
            semantic_regions=semantic_regions,
            vectorizer=vectorizer,
        )
        profile = {
            "requirements": asdict(reqs),
            "coverage": coverage,
            "coverage_engine": cov_engine.to_dict(),
            "calibration_evidence": records,
            "created": rows[-1]["created"] if rows else 0.0,
        }
        profile["id"] = digest(profile)

        self.store.calibrate_candidate_artifact(artifact["id"], canonical(profile))
        logger.info("Calibrated shadow candidate %s for site %s", artifact["id"], resolved_site.version)

        try:
            schema_hash = digest(resolved_site.state_schema)
            choices_hash = digest(resolved_site.choices)
            v_name = getattr(verifier, "__name__", "none") if verifier else "none"
            v_ver = getattr(verifier, "version", "1") if verifier else "1"
            rep_version = "sparse_tfidf_v1" if cov_engine.semantic_regions else "exact_v1"
            self.client._epoch_manager.open_or_get_epoch(
                site=resolved_site.name,
                site_version=resolved_site.version,
                artifact_id=artifact["id"],
                representation_version=rep_version,
                engine=eng_key,
                verifier=v_name,
                verifier_version=v_ver,
                schema_hash=schema_hash,
                choices_hash=choices_hash,
                requirements=asdict(reqs),
            )
            for r in cov_engine.semantic_regions:
                self.client._epoch_manager.sync_region_lifecycle(
                    artifact_id=artifact["id"],
                    site_version=resolved_site.version,
                    region_id=r.region_id,
                    status=r.status,
                    radius=r.radius,
                    negative_margin=r.negative_margin,
                    sample_count=r.member_count,
                )
        except (KeyError, ValueError, OSError) as ex:
            logger.debug("Epoch registration error in calibrate: %s", ex)

        self.client._emit_event(
            "lifecycle",
            {"site": resolved_site.name, "artifact": artifact["id"], "action": "calibrate"},
        )
        return profile

    def evaluate(
        self,
        site: str | DecisionSite,
        *,
        verifier=None,
        auto_promote: bool = True,
    ) -> dict[str, Any]:
        """Evaluates shadow candidate against holdout and shadow traffic with Hoeffding bounds."""
        resolved_site = self.client._resolve(site)
        artifact = self.client._artifact(resolved_site.version)
        if artifact is None or artifact["status"] != "SHADOW" or not artifact["profile"]:
            raise ValueError("Evaluation requires a calibrated shadow candidate")
        profile = artifact["profile"]
        requirements = PromotionRequirements(**profile["requirements"])
        payload = dict(
            artifact["payload"],
            coverage=profile["coverage"],
            coverage_engine=profile.get("coverage_engine"),
        )
        eng_key = resolve_engine_key(payload["engine_data"]["engine"])
        if verifier is None and eng_key != "exact":
            raise ValueError(
                f"Semantic engines require a verifier ({eng_key}). "
                "Pass a callable verifier to evaluate() or maintenance()."
            )
        history = self.store.history(resolved_site.version)
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
        region_stats: dict[str, dict] = {}
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
                        and all(r["choice"] == sem_reg.choice for r in sem_records)
                    ):
                        qualified_sem_regions.append(sem_reg.region_id)
        min_n_for_conf = math.ceil(math.log(20) / (2 * (1.0 - requirements.min_confidence) ** 2))
        qualified = (
            passes(held_stats, requirements)
            and passes(shadow_stats, requirements)
            and all(
                (stats["quality_lower"] >= requirements.min_confidence)
                if stats.get("samples", 0) >= min_n_for_conf
                else (stats.get("quality", 1.0) >= requirements.min_quality)
                for region in region_stats.values()
                for stats in region.values()
                if stats.get("samples", 0) >= requirements.min_region_samples
            )
        )

        # Enforce site-level local_error_budget statistical safety requirement
        if resolved_site.local_error_budget is not None:
            min_site_quality = 1.0 - resolved_site.local_error_budget
            held_q = held_stats.get("quality", 0.0)
            shadow_q = shadow_stats.get("quality", 0.0)
            held_lb = held_stats.get("quality_wilson_lower", held_stats.get("quality_lower", 0.0))
            shadow_lb = shadow_stats.get("quality_wilson_lower", shadow_stats.get("quality_lower", 0.0))
            if held_q < min_site_quality or shadow_q < min_site_quality:
                qualified = False
            elif held_lb < min_site_quality or shadow_lb < min_site_quality:
                qualified = False

        # Enforce utility controller authority denial (e.g. QUALITY_FAIL)
        sel_record = payload.get("selection_record") or {}
        rep_sel = sel_record.get("representation_selection") or sel_record
        if rep_sel.get("serving_authority_denied"):
            qualified = False
        ev_id, ev_id_data = compute_evidence_identity(
            resolved_site, payload, profile, verifier, requirements
        )
        evidence = {
            "profile_id": profile["id"],
            "evidence_identity": ev_id,
            "evidence_identity_data": ev_id_data,
            "holdout": held_stats,
            "shadow": shadow_stats,
            "holdout_records": held_records,
            "shadow_records": shadow_records,
            "qualified": qualified,
            "regions": region_stats,
            "qualified_semantic_regions": qualified_sem_regions,
            "semantic_region_stats": sem_region_stats,
        }

        eval_time = (shadow + holdout)[-1]["created"] if (shadow + holdout) else 0.0
        promotion_tuple = (
            artifact["id"],
            eval_time,
            int(qualified),
            held_stats.get("samples", 0),
            shadow_stats.get("samples", 0),
            held_stats.get("quality_lower"),
            shadow_stats.get("delta_lower"),
            held_stats.get("agreement"),
        )

        self.store.promote_candidate_artifact(
            artifact_id=artifact["id"],
            expected_epoch=artifact["epoch"],
            evidence_json=canonical(evidence),
            promotion_record=promotion_tuple,
            qualified=bool(qualified),
            auto_promote=bool(auto_promote),
            profile_id=profile["id"],
        )

        step = self.store.get_site_decisions_count(resolved_site.version)
        coverage = len(profile.get("coverage", {})) / max(
            1, len(self.store.get_state_coverage(resolved_site.version))
        )
        if qualified:
            self.store.record_authority_event(
                step=step,
                site=resolved_site.version,
                engine=eng_key,
                artifact_id=artifact["id"],
                previous_status="SHADOW",
                new_status="ACTIVE" if auto_promote else "VERIFIED",
                support_n=held_stats.get("samples", 0) + shadow_stats.get("samples", 0),
                quality=held_stats.get("quality"),
                lower_bound=held_stats.get("quality_lower"),
                coverage=coverage,
                reason="qualification evidence passed",
            )
        else:
            if held_stats.get("samples", 0) < requirements.min_samples or shadow_stats.get(
                "samples", 0
            ) < requirements.min_samples:
                non_promotion_reason = "insufficient support"
            elif resolved_site.local_error_budget is not None and (
                held_stats.get("quality", 0.0) < (1.0 - resolved_site.local_error_budget)
                or shadow_stats.get("quality", 0.0) < (1.0 - resolved_site.local_error_budget)
            ):
                non_promotion_reason = "QUALITY_FAIL"
            elif resolved_site.local_error_budget is not None and (
                held_stats.get("quality_wilson_lower", held_stats.get("quality_lower", 0.0))
                < (1.0 - resolved_site.local_error_budget)
                or shadow_stats.get("quality_wilson_lower", shadow_stats.get("quality_lower", 0.0))
                < (1.0 - resolved_site.local_error_budget)
            ):
                non_promotion_reason = "INSUFFICIENT_EVIDENCE"
            elif held_stats.get("quality_lower", 0.0) < requirements.min_quality:
                non_promotion_reason = "quality bound failed"
            elif shadow_stats.get("delta_lower", 0.0) < -requirements.max_degradation:
                non_promotion_reason = "degradation bound failed"
            elif any(
                len({r["choice"] for r in records}) > 1
                for records in (
                    [r for r in held_records if r["region"] == region]
                    for region in profile["coverage"]
                )
            ):
                non_promotion_reason = "region purity failed"
            else:
                non_promotion_reason = "insufficient comparisons"
            self.store.record_non_promotion(
                step=step,
                site=resolved_site.version,
                engine=eng_key,
                artifact_id=artifact["id"],
                reason=non_promotion_reason,
                detail={"holdout": held_stats, "shadow": shadow_stats},
            )

        if qualified:
            if auto_promote:
                logger.info(
                    "Promoted candidate %s to ACTIVE for site %s",
                    artifact["id"],
                    resolved_site.version,
                )
                self.client._emit_event(
                    "lifecycle",
                    {"site": resolved_site.name, "artifact": artifact["id"], "action": "promote"},
                )
            else:
                logger.info(
                    "Marked candidate %s as VERIFIED for site %s",
                    artifact["id"],
                    resolved_site.version,
                )
                self.client._emit_event(
                    "lifecycle",
                    {"site": resolved_site.name, "artifact": artifact["id"], "action": "verify"},
                )
        return evidence

    def reevaluate(self, site: str | DecisionSite) -> dict[str, Any]:
        """Monitors active serving traffic, detects degradation or drift, and triggers demotion."""
        resolved_site = self.client._resolve(site)
        artifact = self.client._artifact(resolved_site.version)
        if artifact is None or artifact["status"] != "ACTIVE":
            return {"demoted": False, "reason": "no_active_path"}
        req = PromotionRequirements(**artifact["profile"]["requirements"])
        rows = [
            r
            for r in self.store.history(resolved_site.version)
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
        stale_identity = False
        if artifact.get("evidence"):
            try:
                ev_raw = artifact["evidence"]
                ev = json.loads(ev_raw) if isinstance(ev_raw, str) else ev_raw
                ev_data = ev.get("evidence_identity_data")
                if ev_data:
                    if resolved_site.fallback_revision != ev_data.get("fallback_revision"):
                        stale_identity = True
                    if tuple(ev_data.get("choices", ())) != resolved_site.choices:
                        stale_identity = True
                    eng_key = resolve_engine_key(artifact["payload"]["engine_data"]["engine"])
                    if eng_key == "decision" and self.engines.get("decision"):
                        curr_rev = (
                            getattr(self.engines["decision"], "checkpoint_hash", None)
                            or getattr(self.engines["decision"], "model_id", None)
                        )
                        if (
                            ev_data.get("model_revision") not in ("none", None, "decision")
                            and curr_rev
                            and str(curr_rev) != ev_data.get("model_revision")
                        ):
                            stale_identity = True
            except (json.JSONDecodeError, TypeError, KeyError):
                pass

        demote = (
            stale_identity
            or (len(rows) >= req.evaluation_window and (missing > 0 or not enough))
            or (
                enough
                and (lower_bound(values) < req.min_quality or delta_lower < -req.max_degradation)
            )
            or (len(rows) >= req.min_samples and unsupported > req.max_uncovered_rate)
            or bool(identities - expected_identities)
        )
        evidence = {
            "demoted": demote,
            "stale_identity": stale_identity,
            "active_samples": len(values),
            "comparison_samples": len(baseline),
            "missing_outcomes": missing,
            "quality_lower": lower_bound(values),
            "delta": delta,
            "delta_lower": delta_lower,
            "uncovered_rate": unsupported,
        }

        if demote:
            demoted = self.store.demote_active_artifact(
                artifact_id=artifact["id"],
                expected_epoch=artifact["epoch"],
                evidence_json=canonical(evidence),
            )
            if demoted:
                logger.warning(
                    "Site %s demoted from ACTIVE to SHADOW (delta_lower=%s, quality_lower=%s)",
                    resolved_site.name,
                    delta_lower,
                    evidence["quality_lower"],
                )
                self.client._emit_event(
                    "lifecycle",
                    {
                        "site": resolved_site.name,
                        "artifact": artifact["id"],
                        "action": "demote",
                        "evidence": evidence,
                    },
                )

        check_time = rows[-1]["created"] if rows else 0.0
        self.store.record_drift_check(
            artifact_id=artifact["id"],
            created=check_time,
            demoted=int(demote),
            active_samples=evidence["active_samples"],
            comparison_samples=evidence["comparison_samples"],
            missing_outcomes=evidence["missing_outcomes"],
            quality_lower=evidence["quality_lower"],
            delta_lower=evidence["delta_lower"],
            uncovered_rate=evidence["uncovered_rate"],
        )
        return evidence
