"""Diagnostics, inspection, health telemetry, and receipts."""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import numpy as np

from ..internal.contracts import (
    DecisionSite,
    PromotionRequirements,
    canonical,
)
from ..internal.coverage import (
    CoverageEngine,
    RegionHealth,
    _extract_text,
)
from ..internal.engines import get_engine_metadata, resolve_engine_key
from ..internal.profiler import profile_history
from ..internal.verification import lower_bound

if TYPE_CHECKING:
    from ..client import Ink

logger = logging.getLogger("ink.runtime.diagnostics")


class Diagnostics:
    """Read-only inspection, status, health formatting, and audit receipts."""

    def __init__(self, client: Ink) -> None:
        self.client = client

    @property
    def store(self):
        return self.client.store

    @property
    def engines(self):
        return self.client.engines

    def sites(self) -> list[dict[str, Any]]:
        """Inspects all registered sites in the database."""
        return [
            self.inspect(DecisionSite(**json.loads(r["contract"])))
            for r in self.store.get_all_sites()
        ]

    def profile(self, site: str | DecisionSite) -> dict[str, Any]:
        """Historical latency, token, and error profile for a decision site."""
        resolved_site = self.client._resolve(site)
        return profile_history(self.store.history(resolved_site.version))

    def coverage(self, site: str | DecisionSite) -> list[dict[str, Any]]:
        """Queryable per-state counters: observations, fast serves, outcomes, quality."""
        resolved_site = self.client._resolve(site)
        return self.store.get_state_coverage(resolved_site.version)

    def promotions(self, site: str | DecisionSite) -> list[dict[str, Any]]:
        """Queryable promotion decisions per artifact, newest first."""
        resolved_site = self.client._resolve(site)
        return self.store.get_promotion_records(resolved_site.version)

    def drift_history(self, site: str | DecisionSite) -> list[dict[str, Any]]:
        """Queryable re-evaluation ticks per artifact, newest first."""
        resolved_site = self.client._resolve(site)
        return self.store.get_drift_checks(resolved_site.version)

    def lineage(self, site: str | DecisionSite) -> list[dict[str, Any]]:
        """Artifact parent-to-child links from recompilation replacements."""
        resolved_site = self.client._resolve(site)
        return self.store.get_artifact_links(resolved_site.version)

    def receipt(self, decision_id: str) -> dict[str, Any]:
        """Provides an authoritative provenance and verification receipt for a decision."""
        row = self.store.get_decision(decision_id)
        if not row:
            raise KeyError(f"Unknown decision ID: {decision_id}")
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

    def inspect(self, site: str | DecisionSite) -> dict[str, Any]:
        """Comprehensive developer-facing inspection report of a site's lifecycle and safety."""
        resolved_site = self.client._resolve(site)
        rows = self.store.history(resolved_site.version)
        try:
            artifact = self.client._artifact(resolved_site.version)
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
        blocker = "none"
        if artifact is None:
            b = self.client._evaluator._compile_blocker(resolved_site)
            blocker = b if b is not None else "ready_to_compile"
        elif artifact["status"] == "SHADOW":
            if artifact["profile"] is None:
                eng = resolve_engine_key(artifact["payload"]["engine_data"]["engine"])
                if eng != "exact" and self.client._maintenance_verifier is None:
                    blocker = "verifier_unavailable"
                else:
                    blocker = "waiting_for_calibration"
            else:
                req = PromotionRequirements(**artifact["profile"]["requirements"])
                payload = artifact["payload"]
                shadow = [
                    r
                    for r in rows
                    if r["artifact"] == artifact["id"]
                    and r["created"] > artifact["epoch"]
                    and r["prediction"] is not None
                    and r["source"] == "fallback"
                ]
                used_tasks = {
                    r["task"]
                    for r in rows
                    if r["id"] in set(sum(payload["partitions"].values(), []))
                }
                shadow = [r for r in shadow if r["task"] not in used_tasks]
                missing = any(
                    r["outcome"] is None
                    for r in shadow
                    if canonical(r["state"]) in artifact["profile"]["coverage"]
                )
                shadow_tasks = {r["task"] for r in shadow}
                if missing:
                    blocker = "waiting_for_outcomes"
                elif len(shadow_tasks) < req.min_samples:
                    blocker = f"waiting_for_shadow_samples ({len(shadow_tasks)}/{req.min_samples})"
                else:
                    blocker = "ready_to_evaluate"
        elif artifact["status"] == "ACTIVE":
            if artifact.get("profile"):
                req = PromotionRequirements(**artifact["profile"]["requirements"])
                recent = [
                    r
                    for r in rows
                    if r["artifact"] == artifact["id"] and r["created"] > artifact["epoch"]
                ][-req.evaluation_window :]
                comp_rows = [r for r in recent if r["reason"] == "comparison"]
                if comp_rows and any(r["outcome"] is None for r in comp_rows):
                    blocker = "waiting_for_comparison_outcomes"
                else:
                    blocker = "none (active)"
            else:
                blocker = "none (active)"
        else:
            blocker = f"status_{artifact['status'].lower()}"

        return {
            "name": resolved_site.name,
            "version": resolved_site.version,
            "contract": asdict(resolved_site),
            "model": {
                "enabled": self.client._model_enabled,
                "loaded": bool(
                    self.engines.get("decision")
                    and getattr(self.engines["decision"], "_agents", {})
                ),
                "implementation": (
                    self.engines["decision"].name if self.engines.get("decision") else None
                ),
                "policy_model_id": getattr(getattr(self.engines.get("decision"), "backend", None), "model_id", None),
                "backend": getattr(getattr(self.engines.get("decision"), "backend", None), "backend_name", None),
            },
            "state": artifact["status"] if artifact else "OBSERVE",
            "blocker": blocker,
            "active_revision": (
                artifact["id"] if artifact and artifact["status"] == "ACTIVE" else None
            ),
            "error": error,
            "fast_path": artifact["id"] if artifact else None,
            "observations": len(rows),
            "outcomes": sum(r["outcome"] is not None for r in rows),
            "unique_states": len({canonical(r["state"]) for r in rows}),
            "choices": dict(Counter(r["choice"] for r in rows)),
            "coverage": len(fast) / len(rows) if rows else 0,
            "fallbacks": len(rows) - len(fast),
            "fallbacks_avoided": len(fast),
            "verified_fast_path_decisions": len(verified),
            "model_calls_avoided": len(fast) * resolved_site.fallback_model_calls
            if resolved_site.fallback_model_calls is not None
            else None,
            "verified_model_calls_avoided": len(verified) * resolved_site.fallback_model_calls
            if resolved_site.fallback_model_calls is not None
            else None,
            "savings_basis": "declared_and_validated_fixed_call_count"
            if resolved_site.fallback_model_calls is not None
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
            "lifecycle": self.store.get_events(artifact["id"])
            if artifact
            else [],
            "auto_maintenance": self.client._auto_maintenance,
            "selected_engine": artifact["payload"]["engine_data"]["engine"] if artifact else None,
            "engine_metadata": get_engine_metadata(
                artifact["payload"]["engine_data"]["engine"],
                payload=artifact["payload"]["engine_data"],
            )
            if artifact and "engine_data" in artifact["payload"]
            else None,
            "policy_model_id": (
                getattr(getattr(self.client.engines.get("decision"), "backend", None), "model_id", None)
                if self.client._model_enabled else None
            ),
            "policy_model_version": (
                getattr(getattr(self.client.engines.get("decision"), "backend", None), "model_version", None)
                if self.client._model_enabled else None
            ),
            "checkpoint_revision": (
                getattr(getattr(self.client.engines.get("decision"), "backend", None), "checkpoint_revision", None)
                if self.client._model_enabled else None
            ),
            "policy_backend": (
                getattr(getattr(self.client.engines.get("decision"), "backend", None), "backend_name", None)
                if self.client._model_enabled else None
            ),
            "representation_version": artifact["payload"].get("representation", {}).get("version", "none")
            if artifact
            else None,
            "exact_coverage_count": len(artifact["profile"].get("coverage", {}))
            if artifact and artifact.get("profile")
            else 0,
            "semantic_region_count": len(
                artifact["profile"].get("coverage_engine", {}).get("semantic_regions", [])
            )
            if artifact and artifact.get("profile")
            else 0,
            "qualified_semantic_region_count": len(
                (json.loads(artifact["evidence"]) if isinstance(artifact.get("evidence"), str) else (artifact.get("evidence") or {})).get("qualified_semantic_regions", [])
            )
            if artifact and artifact.get("evidence")
            else 0,
            "selection_record": artifact["payload"].get("selection_record") if artifact else None,
        }

    def status(self, site: str | DecisionSite) -> dict[str, Any]:
        """Concise one-line summary of site status and operational volume."""
        insp = self.inspect(site)
        return {
            "name": insp["name"],
            "version": insp["version"],
            "model_enabled": self.client._model_enabled,
            "state": insp["state"],
            "blocker": insp["blocker"],
            "active_revision": insp["active_revision"],
            "active_candidate": insp["active_revision"],
            "observations": insp["observations"],
            "outcomes": insp["outcomes"],
            "outcome_coverage": insp["outcome_completeness"],
            "fast_path": insp["fast_path"],
            "fast_served": insp["fallbacks_avoided"],
            "fallbacks": insp["fallbacks"],
            "auto_maintenance": self.client._auto_maintenance,
        }

    def health(self, site: str | DecisionSite) -> dict[str, Any]:
        """Aggregated operational health and economic metrics for a decision site."""
        resolved_site = self.client._resolve(site)
        insp = self.inspect(resolved_site)
        status = insp["state"]
        drift_rows = self.drift_history(resolved_site)
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

        rows = self.store.history(resolved_site.version)
        fast_rows = [r for r in rows if r["source"] == "fast_path"]
        false_serves = sum(
            1 for r in fast_rows if r["outcome"] and r["outcome"].get("quality", 1.0) < 1.0
        )

        return {
            "name": resolved_site.name,
            "version": resolved_site.version,
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

    def fleet_health(self) -> dict[str, Any]:
        """Aggregates health across all known DecisionSites in the database."""
        site_healths = {}
        active_count = 0
        shadow_count = 0
        observe_count = 0
        demoted_count = 0
        total_obs = 0
        total_fast = 0
        total_false = 0

        for row in self.store.get_all_sites():
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

    def region_health(self, site: str | DecisionSite, region_id: str) -> dict[str, Any]:
        """Per-region geometry, distance percentiles, and verified quality breakdown."""
        resolved_site = self.client._resolve(site)
        artifact = self.client._artifact(resolved_site.version)
        if not artifact or not artifact.get("profile"):
            raise KeyError(f"No calibrated artifact found for site {resolved_site.name}")
        cov_data = artifact["profile"].get("coverage_engine")
        if not cov_data:
            raise KeyError(f"No semantic coverage engine for site {resolved_site.name}")
        cov_engine = CoverageEngine.from_dict(cov_data)
        reg = cov_engine.get_region(region_id)
        if not reg:
            raise KeyError(f"Region {region_id} not found in coverage engine")

        rows = self.store.history(resolved_site.version)
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
        disagreements = [r for r in comparisons if r.get("outcome") and r["choice"] != reg.choice]
        disagreement_rate = len(disagreements) / len(comparisons) if comparisons else 0.0

        dist_mean = float(np.mean(dists)) if dists else 0.0
        dist_p95 = float(np.percentile(dists, 95)) if dists else 0.0

        latest_drift = self.drift_history(resolved_site)
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

    def all_region_health(self, site: str | DecisionSite) -> list[dict[str, Any]]:
        """Health reports across all semantic regions belonging to the calibrated site."""
        resolved_site = self.client._resolve(site)
        artifact = self.client._artifact(resolved_site.version)
        if not artifact or not artifact.get("profile"):
            return []
        cov_data = artifact["profile"].get("coverage_engine")
        if not cov_data:
            return []
        cov_engine = CoverageEngine.from_dict(cov_data)
        return [self.region_health(resolved_site, reg.region_id) for reg in cov_engine.semantic_regions]

    def utility(self) -> dict[str, Any]:
        """Diagnostics regarding policy model utility gating."""
        if hasattr(self.client, "utility_controller"):
            return self.client.utility_controller.diagnostics
        return {
            "small_representation_calls": 0,
            "small_enabled_sites": 0,
            "small_disabled_sites": 0,
            "small_compute_ms": 0.0,
        }
