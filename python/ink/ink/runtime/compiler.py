"""Artifact compilation, engine evaluation, and candidate selection."""

from __future__ import annotations

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
    TextVectorizer,
    calibrate_semantic_boundaries,
)
from ..internal.decision_store import split_history
from ..internal.engines import resolve_engine_key
from ..internal.representation import RepresentationStrategy, StateRepresentationLayer
from ..internal.selection import (
    EngineSelectionPolicy,
    evaluate_engine_candidate,
)

if TYPE_CHECKING:
    from ..client import Ink

logger = logging.getLogger("ink.runtime.compiler")

DEFAULT_REQUIREMENTS = PromotionRequirements(
    min_samples=10,
    min_quality=0.8,
    min_confidence=0.7,
    max_degradation=0.15,
    comparison_rate=0.25,
    min_region_samples=3,
    evaluation_window=50,
)


def compute_evidence_identity(
    site: DecisionSite,
    payload: dict,
    profile: dict | None = None,
    verifier=None,
    requirements: PromotionRequirements | None = None,
) -> tuple[str, dict]:
    """Computes immutable SHA-256 identity digest of contract, engine, verifier, and requirements."""
    eng_data = payload.get("engine_data", {})
    eng_name = eng_data.get("engine", "exact")
    policy_model_id = eng_data.get("policy_model_id") or eng_data.get("model") or "none"
    checkpoint_revision = (
        eng_data.get("checkpoint_revision")
        or eng_data.get("revision")
        or eng_data.get("checkpoint")
        or "none"
    )
    manifest_sha256 = eng_data.get("manifest_sha256") or "none"
    model_rev = (
        eng_data.get("revision")
        or eng_data.get("checkpoint")
        or "none"
    )
    v_name = (
        verifier.__name__
        if hasattr(verifier, "__name__")
        else (str(verifier) if verifier is not None else "observed")
    )
    v_ver = getattr(verifier, "version", "1") or "1"
    req_data = (
        asdict(requirements)
        if requirements
        else (profile.get("requirements") if profile else {})
    )
    cov_rev = (
        profile.get("coverage_engine", {}).get("version", "v1")
        if profile and profile.get("coverage_engine")
        else "v1"
    )
    data = {
        "site_version": site.version,
        "choices": list(site.choices),
        "fallback_revision": site.fallback_revision,
        "engine": eng_name,
        "policy_model_id": str(policy_model_id),
        "checkpoint_revision": str(checkpoint_revision),
        "manifest_sha256": str(manifest_sha256),
        "model_revision": str(model_rev),
        "verifier": str(v_name),
        "verifier_version": str(v_ver),
        "requirements": req_data,
        "coverage_revision": cov_rev,
    }
    return digest(data), data


class Compiler:
    """Compiles observation histories into candidate decision artifacts."""

    def __init__(self, client: Ink) -> None:
        self.client = client

    @property
    def store(self):
        return self.client.store

    @property
    def engines(self):
        return self.client.engines

    def compile(
        self,
        site: str | DecisionSite,
        *,
        engine: str = "auto",
        replace_existing: bool = False,
    ) -> str:
        """Extracts history partitions, fits representation, selects engine, and persists candidate."""
        resolved_site = self.client._resolve(site)
        rows = self.store.history(resolved_site.version)
        eligible = self._verified_training_rows(rows)
        train, calibration, evaluation = split_history(eligible)
        if not all((train, calibration, evaluation)):
            raise ValueError("Need outcome-bearing task groups in all three partitions")

        rep_layer = StateRepresentationLayer(resolved_site.state_schema).fit_from_records(train)
        texts = [rep_layer.extract_semantic_text(r["state"]) for r in train]
        vectorizer = TextVectorizer.fit(texts)
        candidate_sem_regions = calibrate_semantic_boundaries(
            train,
            vectorizer,
            resolved_site.version,
            min_region_samples=1,
        )
        cov_engine = CoverageEngine(
            exact_coverage={canonical(r["state"]): {} for r in train},
            semantic_regions=candidate_sem_regions,
            vectorizer=vectorizer,
        )

        engine_key = resolve_engine_key(engine)
        if engine_key == "decision":
            raise ValueError(
                "Policy Models are control-plane inputs; compile Exact or Linear serving artifacts"
            )
        selection_record = None

        if engine_key in ("auto", "select"):
            reqs = self.client._maintenance_requirements or DEFAULT_REQUIREMENTS
            candidate_names = ["exact", "linear"]
            evaluations = {}
            for c_name in candidate_names:
                eng = self.engines.get(c_name)
                if eng is not None:
                    try:
                        p_load, report = evaluate_engine_candidate(
                            c_name, eng, resolved_site, train, calibration, reqs, rep_layer=rep_layer
                        )
                        evaluations[c_name] = (p_load, report)
                    except (ValueError, KeyError, RuntimeError) as e:
                        logger.warning("Error evaluating candidate engine %s: %s", c_name, e)
            if evaluations:
                engine_key, engine_payload, selection_record = EngineSelectionPolicy.select_best_engine(
                    evaluations, reqs
                )
                if engine_key in ("linear", "classifier"):
                    engine_payload, rep_selection = self._compile_best_linear_representation(
                        resolved_site, train, calibration, rep_layer
                    )
                    selection_record = {
                        "engine_selection": selection_record,
                        "representation_selection": rep_selection,
                    }
            else:
                engine_key = "exact"
                engine_payload = self.engines["exact"].compile(resolved_site, train)
        else:
            if engine_key in ("linear", "classifier"):
                engine_payload, selection_record = self._compile_best_linear_representation(
                    resolved_site, train, calibration, rep_layer
                )
            else:
                engine_payload = self.engines[engine_key].compile(resolved_site, train)

        payload = {
            "site": resolved_site.version,
            "choices": list(resolved_site.choices),
            "engine_data": engine_payload,
            "coverage": {canonical(r["state"]): {} for r in train},
            "coverage_engine": cov_engine.to_dict(),
            "representation": rep_layer.to_dict(),
            "selection_record": selection_record,
            "partitions": {
                name: [r["id"] for r in part]
                for name, part in (
                    ("train", train),
                    ("calibration", calibration),
                    ("evaluation", evaluation),
                )
            },
            "dataset_digest": digest(eligible),
            "created": rows[-1]["created"] if rows else 0.0,
        }
        artifact_id = digest(payload)

        self.store.compile_candidate_artifact(
            artifact_id=artifact_id,
            site_version=resolved_site.version,
            payload_json=canonical(payload),
            checksum=digest(payload),
            engine_key=engine_key,
            replace_existing=replace_existing,
        )

        logger.info(
            "Compiled candidate artifact %s for site %s (engine=%s)",
            artifact_id,
            resolved_site.version,
            engine_key,
        )
        self.client._emit_event(
            "lifecycle",
            {"site": resolved_site.name, "artifact": artifact_id, "action": "compile", "engine": engine_key},
        )
        return artifact_id

    @staticmethod
    def _verified_training_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build labels only from verified outcomes, never raw Small proposals."""
        verified = []
        for row in rows:
            if row["source"] != "fallback" or row["outcome"] is None:
                continue
            outcome = row["outcome"]
            expected = (outcome.get("evidence") or {}).get("expected")
            if expected is None and float(outcome.get("quality", 0.0)) <= 0.0:
                continue
            item = dict(row)
            item["choice"] = expected or row["choice"]
            verified.append(item)
        return verified

    def _compile_best_linear_representation(
        self, site, train, calibration, rep_layer
    ) -> tuple[dict, dict]:
        """Ablate available feature families on verified calibration labels."""
        observations = self.store.get_policy_observations(site.version, verified_only=True)
        policy_vectors = {
            canonical(row["state"]): row["representation"]
            for row in observations
            if row["representation"] is not None
        }
        strategies = [
            RepresentationStrategy.LEXICAL,
            RepresentationStrategy.STRUCTURED_LEXICAL,
        ]
        if (
            policy_vectors
            and all(canonical(row["state"]) in policy_vectors for row in train)
            and self.client.utility_controller.is_small_active(site)
        ):
            strategies.extend(
                [RepresentationStrategy.SMALL, RepresentationStrategy.STRUCTURED_SMALL]
            )
        reports = []
        payloads = {}
        engine = self.engines["linear"]
        for strategy in strategies:
            payload = engine.compile(
                site,
                train,
                representation_layer=rep_layer,
                representation_strategy=strategy.value,
                policy_vectors=policy_vectors,
            )
            correct = 0
            attempted = 0
            for row in calibration:
                try:
                    choice, _ = engine.predict(payload, row["state"])
                except ValueError:
                    continue
                attempted += 1
                correct += int(choice == row["choice"])
            accuracy = correct / attempted if attempted else 0.0
            report = {
                "strategy": strategy.value,
                "samples": attempted,
                "correct": correct,
                "verified_accuracy": accuracy,
                "uses_small": strategy.uses_small,
            }
            reports.append(report)
            payloads[strategy.value] = payload
        best = max(
            reports,
            key=lambda report: (
                report["verified_accuracy"],
                report["samples"],
                not report["uses_small"],
            ),
        )

        classical_reports = [r for r in reports if not r["uses_small"]]
        small_reports = [r for r in reports if r["uses_small"]]
        classical_score = max([r["verified_accuracy"] for r in classical_reports], default=0.0)
        small_score = max([r["verified_accuracy"] for r in small_reports], default=0.0)

        decision = "DISABLED"
        evidence = None
        if small_reports:
            from ..internal.verification import wilson_lower_bound
            best_n = best["samples"]
            best_correct = best["correct"]
            best_lb = wilson_lower_bound(best_correct, best_n) if best_n else 0.0
            decision, evidence = self.client.utility_controller.evaluate_representation_utility(
                site,
                classical_score=classical_score,
                small_score=small_score,
                candidate_quality=best["verified_accuracy"],
                candidate_lower_bound=best_lb,
            )

        return payloads[best["strategy"]], {
            "kind": "representation_ablation",
            "selected": best["strategy"],
            "candidates": reports,
            "label_source": "verified_outcome",
            "utility_decision": decision,
            "utility_evidence": asdict(evidence) if evidence else None,
            "serving_authority_denied": (decision in ("DISABLED_FOR_SERVING_UTILITY", "INSUFFICIENT_EVIDENCE")),
        }

    def _compile_blocker(
        self, site: str | DecisionSite, requirements: PromotionRequirements | None = None
    ) -> str | None:
        """Identifies pre-requisite blockers preventing candidate compilation."""
        resolved_site = self.client._resolve(site)
        req = requirements or self.client._maintenance_requirements or DEFAULT_REQUIREMENTS
        rows = self.store.history(resolved_site.version)
        if not rows:
            return "waiting_for_observations"
        eligible = self._verified_training_rows(rows)
        if not eligible:
            return "waiting_for_outcomes"
        train, calibration, evaluation = split_history(eligible)
        if not all((train, calibration, evaluation)):
            return "insufficient_task_groups"
        if req.min_confidence == 1 or req.max_degradation == 0:
            return "impossible_bounds"
        calibration_min = max(
            req.min_region_samples,
            math.ceil(math.log(20) / (2 * (1 - req.min_confidence) ** 2)),
        )
        evaluation_min = max(
            req.min_samples, math.ceil(2 * math.log(20) / req.max_degradation ** 2)
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
            return "insufficient_samples_per_region"
        return None

    def _require_compilation_support(
        self, site: str | DecisionSite, requirements: PromotionRequirements | None
    ) -> None:
        """Validates that candidate compilation has adequate statistical sample support."""
        blocker = self._compile_blocker(site, requirements)
        if blocker == "impossible_bounds":
            raise ValueError("Finite samples cannot meet the configured confidence bounds")
        if blocker is not None:
            raise ValueError("Insufficient independent calibration/evaluation tasks to compile")
