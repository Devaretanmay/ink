"""Lifecycle coordination, self-tuning maintenance, and evaluation facade."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from ..internal.contracts import (
    DecisionSite,
    MaintenanceOutcome,
    PromotionRequirements,
    canonical,
    digest,
)
from ..internal.coverage import CoverageEngine
from ..internal.engines import resolve_engine_key
from .compiler import DEFAULT_REQUIREMENTS, Compiler, compute_evidence_identity
from .qualification import QualificationOrchestrator

__all__ = [
    "DEFAULT_REQUIREMENTS",
    "Evaluator",
    "MaintenanceOutcome",
    "compute_evidence_identity",
]

if TYPE_CHECKING:
    from ..client import Ink

logger = logging.getLogger("ink.runtime.evaluator")

__all__ = ["Evaluator", "MaintenanceOutcome", "DEFAULT_REQUIREMENTS"]


class Evaluator:
    """Coordinator façade for compilation, qualification, and periodic self-tuning maintenance."""

    def __init__(self, client: Ink) -> None:
        self.client = client
        self.compiler = Compiler(client)
        self.qualification = QualificationOrchestrator(client)

    @property
    def store(self):
        return self.client.store

    @property
    def engines(self):
        return self.client.engines

    # Compilation Delegation
    def compile(
        self,
        site: str | DecisionSite,
        *,
        engine: str = "auto",
        replace_existing: bool = False,
    ) -> str:
        return self.compiler.compile(site, engine=engine, replace_existing=replace_existing)

    def _compile_blocker(
        self, site: str | DecisionSite, requirements: PromotionRequirements | None = None
    ) -> str | None:
        return self.compiler._compile_blocker(site, requirements=requirements)

    def _require_compilation_support(
        self, site: str | DecisionSite, requirements: PromotionRequirements | None = None
    ) -> None:
        self.compiler._require_compilation_support(site, requirements)

    # Qualification Delegation
    def calibrate(
        self,
        site: str | DecisionSite,
        *,
        verifier=None,
        requirements: PromotionRequirements | None = None,
    ) -> dict[str, Any]:
        return self.qualification.calibrate(site, verifier=verifier, requirements=requirements)

    def evaluate(
        self,
        site: str | DecisionSite,
        *,
        verifier=None,
        auto_promote: bool = True,
    ) -> dict[str, Any]:
        return self.qualification.evaluate(site, verifier=verifier, auto_promote=auto_promote)

    def reevaluate(self, site: str | DecisionSite) -> dict[str, Any]:
        return self.qualification.reevaluate(site)

    # Invalidation, Hot-swap, and Compaction
    def invalidate(
        self,
        site: str | DecisionSite,
        *,
        reason: str = "policy_revision",
        action: str = "demote",
    ) -> dict[str, Any]:
        resolved_site = self.client._resolve(site)
        artifact = self.client._artifact(resolved_site.version)
        if artifact is None or artifact["status"] not in ("ACTIVE", "VERIFIED"):
            return {"site": resolved_site.name, "invalidated": False, "reason": "no_active_artifact"}
        target_status = "RETIRED" if action == "retire" else "SHADOW"
        self.store.invalidate_artifact(artifact["id"], artifact["status"], target_status, reason)
        logger.info(
            "Invalidated site %s: target=%s, reason=%s",
            resolved_site.name,
            target_status,
            reason,
        )
        self.client._emit_event(
            "lifecycle",
            {
                "site": resolved_site.name,
                "artifact": artifact["id"],
                "action": "invalidate",
                "target": target_status,
                "reason": reason,
            },
        )
        return {
            "site": resolved_site.name,
            "version": resolved_site.version,
            "artifact": artifact["id"],
            "previous_status": artifact["status"],
            "new_status": target_status,
            "reason": reason,
            "invalidated": True,
        }

    def hot_swap(self, site: str | DecisionSite, new_artifact_id: str) -> str:
        resolved_site = self.client._resolve(site)
        return self.store.hot_swap_artifact(resolved_site.version, new_artifact_id)

    def compact(
        self,
        site: str | DecisionSite | None = None,
        *,
        keep_recent: int = 1000,
        before_timestamp: float | None = None,
        max_age_days: float | None = None,
        vacuum: bool = False,
    ) -> dict[str, Any]:
        if max_age_days is not None:
            before_timestamp = time.time() - (max_age_days * 86400.0)
        if site is None:
            all_sites = [
                DecisionSite(**json.loads(r["contract"]))
                for r in self.store.get_all_sites()
            ]
            pruned_total = 0
            remaining_total = 0
            for s in all_sites:
                res = self.compact(
                    s,
                    keep_recent=keep_recent,
                    before_timestamp=before_timestamp,
                    vacuum=False,
                )
                pruned_total += res["pruned"]
                remaining_total += res["remaining"]
            if vacuum:
                self.store.vacuum()
            return {"site": "all", "pruned": pruned_total, "remaining": remaining_total}

        resolved_site = self.client._resolve(site)
        pruned_count, remaining = self.store.compact_site_decisions(
            resolved_site.version,
            keep_recent=keep_recent,
            before_timestamp=before_timestamp,
        )
        if vacuum:
            self.store.vacuum()

        logger.info(
            "Compacted site %s: pruned %d, remaining %d",
            resolved_site.name,
            pruned_count,
            remaining,
        )
        self.client._emit_event(
            "lifecycle",
            {
                "site": resolved_site.name,
                "action": "compact",
                "pruned": pruned_count,
                "remaining": remaining,
            },
        )
        return {
            "site": resolved_site.name,
            "version": resolved_site.version,
            "pruned": pruned_count,
            "remaining": remaining,
        }

    # Maintenance Scheduling & Self-Tuning
    def maintenance(
        self,
        sites: list[str | DecisionSite] | None = None,
        *,
        max_sites: int | None = None,
        max_rows: int | None = None,
        time_budget_sec: float | None = None,
        verifier=None,
        requirements: PromotionRequirements | None = None,
        engine: str = "auto",
    ) -> dict[str, MaintenanceOutcome]:
        results: dict[str, MaintenanceOutcome] = {}
        chosen_engine = (
            self.client._maintenance_engine
            if (getattr(self.client, "_maintenance_engine", None) and engine == "auto")
            else engine
        )
        engine = resolve_engine_key(chosen_engine)
        if sites is not None:
            resolved_sites = [self.client._resolve(s) for s in sites]
        else:
            resolved_sites = [
                DecisionSite(**json.loads(r["contract"]))
                for r in self.store.get_all_sites()
            ]

        def site_priority_key(site: DecisionSite):
            art = self.client._artifact(site.version)
            st = art["status"] if art else "OBSERVE"
            drift = self.client.drift_history(site)
            last_drift = drift[0]["created"] if drift else 0.0
            hist = self.store.history(site.version)
            obs_count = len(hist)
            last_visit = self.client._maintenance_visited.get(site.version, 0.0)
            if st == "ACTIVE":
                return (0, last_drift, last_visit, -obs_count, site.name)
            if st == "SHADOW":
                s_out = sum(1 for r in hist if art and r["created"] > art["epoch"] and r["prediction"] is not None and r["outcome"] is not None)
                return (1, -s_out, last_visit, site.name)
            if st == "CANDIDATE":
                return (2, -obs_count, last_visit, site.name)
            if st == "OBSERVE":
                return (3, last_visit, -obs_count, site.name)
            return (4, last_visit, site.name)

        prioritized = sorted(resolved_sites, key=site_priority_key)
        start_time = time.perf_counter()
        total_rows = 0

        for site in prioritized:
            if max_sites is not None and len(results) >= max_sites:
                results[site.name] = MaintenanceOutcome({"deferred": "max_sites_reached"})
                continue
            if time_budget_sec is not None and (time.perf_counter() - start_time) >= time_budget_sec:
                results[site.name] = MaintenanceOutcome({"deferred": "time_budget_exceeded"})
                continue
            if max_rows is not None and total_rows >= max_rows:
                results[site.name] = MaintenanceOutcome({"deferred": "max_rows_reached"})
                continue

            self.client._maintenance_visited[site.version] = time.time()
            site_rows = len(self.store.history(site.version))
            total_rows += site_rows
            try:
                artifact = self.client._artifact(site.version)
                req = requirements or self.client._maintenance_requirements or DEFAULT_REQUIREMENTS
                ver = verifier if verifier is not None else self.client._maintenance_verifier
                if artifact is None:
                    blocker = self._compile_blocker(site, req)
                    if blocker is not None:
                        results[site.name] = MaintenanceOutcome({"pending": blocker})
                        continue
                    artifact_id = self.compile(site, engine=engine)
                    results[site.name] = MaintenanceOutcome({"compiled": artifact_id})
                    artifact = self.client._artifact(site.version)
                    if ver is not None or engine == "exact":
                        try:
                            self.calibrate(site, verifier=ver, requirements=req)
                        except ValueError:
                            pass
                elif artifact["status"] == "ACTIVE":
                    reeval = self.reevaluate(site)
                    results[site.name] = MaintenanceOutcome(reeval)
                    cur = self.client._artifact(site.version)

                    if cur and cur["status"] == "ACTIVE" and cur.get("profile"):
                        prof = cur["profile"]
                        cov_data = prof.get("coverage_engine")
                        if cov_data:
                            cov_engine = CoverageEngine.from_dict(cov_data)
                            history = self.store.history(site.version)

                            def on_tune_event(
                                ev_name: str,
                                ev_data: dict[str, Any],
                                _target_id: str = cur["id"],
                            ) -> None:
                                self.store.record_event(
                                    _target_id,
                                    "ACTIVE",
                                    "ACTIVE",
                                    json.dumps({"event": ev_name, **ev_data}),
                                )

                            if cov_engine.self_tune_from_history(
                                history, prof.get("requirements", {}), on_event=on_tune_event
                            ):
                                prof["coverage_engine"] = cov_engine.to_dict()
                                prof.pop("id", None)
                                prof["id"] = digest(prof)
                                self.store.update_active_artifact_profile(cur["id"], canonical(prof))
                                results[site.name]["self_tuned"] = True
                else:
                    eng = resolve_engine_key(artifact["payload"]["engine_data"]["engine"])
                    if ver is None and eng != "exact":
                        results[site.name] = MaintenanceOutcome({"pending": "verifier_required"})
                        continue
                    if artifact["profile"] is None:
                        try:
                            self.calibrate(site, verifier=ver, requirements=req)
                            artifact = self.client._artifact(site.version)
                        except ValueError as error:
                            if (
                                str(error)
                                != "No state regions have sufficient calibration evidence"
                            ):
                                raise
                            self._require_compilation_support(site, req)
                            history = self.store.history(site.version)
                            new_rows = [
                                r
                                for r in history
                                if r["created"] > artifact["payload"]["created"]
                                and r["outcome"] is not None
                            ]
                            if len(new_rows) < req.min_samples:
                                raise
                            self.compile(site, engine=engine, replace_existing=True)
                            self.calibrate(site, verifier=ver, requirements=req)
                            artifact = self.client._artifact(site.version)
                    prof = artifact.get("profile")
                    req_dict = prof.get("requirements", {}) if prof else {}
                    if artifact.get("profile") is not None and (
                        req_dict.get("high_risk", False)
                        or not req_dict.get("allow_auto_requalify", True)
                    ):
                        results[site.name] = MaintenanceOutcome(
                            {"pending": "auto_requalify_disabled_for_site"}
                        )
                    else:
                        eval_res = self.evaluate(site, verifier=ver)
                        results[site.name] = MaintenanceOutcome(eval_res)
            except (ValueError, KeyError, OSError) as error:
                results[site.name] = MaintenanceOutcome({"pending": str(error)})

        return results
