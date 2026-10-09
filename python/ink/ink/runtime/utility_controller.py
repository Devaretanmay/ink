"""Production Policy Utility Controller for Quality-Aware Small Policy-Model Auto-Enablement.

Owns the DecisionSite lifecycle state:
    UNKNOWN -> EVALUATING -> ENABLED -> DISABLED -> DEGRADED

Responsibilities:
1. Gathers and compares classical vs Small representation evidence.
2. Enforces site-level `local_error_budget` as a mandatory safety gate.
3. Decides whether Small representations should be extracted for future compilation epochs.
4. Halts Small inferences when a site is DISABLED or FAILS safety.
5. Persists utility evidence scoped by app, site, version, schema, and model revision.
6. Handles drift reevaluation and model invalidation triggers.
7. Small has ZERO serving authority. Fast path remains strictly Exact -> Linear -> Host.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Any

from ..internal.contracts import (
    DecisionSite,
    PolicyRepresentationState,
    PolicyUtilityEvidence,
    canonical,
    digest,
)

if TYPE_CHECKING:
    from ..client import Ink

logger = logging.getLogger("ink.runtime.utility_controller")


def compute_utility_scope_key(
    app: str,
    site_name: str,
    site_version: str,
    choices: list[str] | tuple[str, ...],
    representation_revision: str,
    checkpoint_revision: str,
) -> str:
    """Computes immutable SHA-256 identity digest for utility compatibility scoping."""
    payload = {
        "app": str(app),
        "site": str(site_name),
        "site_version": str(site_version),
        "choices": sorted(list(choices)),
        "representation_revision": str(representation_revision),
        "checkpoint_revision": str(checkpoint_revision),
    }
    return digest(payload)


class PolicyUtilityController:
    """Production runtime controller managing per-site Small representation utility."""

    def __init__(self, client: Ink) -> None:
        self.client = client
        self._lock = threading.RLock()
        self._site_states: dict[str, str] = {}
        self._site_evidence: dict[str, PolicyUtilityEvidence] = {}
        self._representation_revision = "dense_embedding_v1"

        # Diagnostics counters
        self.small_representation_calls = 0
        self.small_compute_ms = 0.0

    def _get_checkpoint_revision(self) -> str:
        engine = self.client.engines.get("decision")
        backend = getattr(engine, "backend", None)
        if backend is not None:
            return getattr(backend, "checkpoint_revision", "phase18-full-kd") or "phase18-full-kd"
        return "phase18-full-kd"

    def get_scope_key(self, site: DecisionSite) -> str:
        return compute_utility_scope_key(
            app=self.client.app_id,
            site_name=site.name,
            site_version=site.version,
            choices=site.choices,
            representation_revision=self._representation_revision,
            checkpoint_revision=self._get_checkpoint_revision(),
        )

    def get_state(self, site: DecisionSite) -> PolicyRepresentationState:
        """Retrieves the current PolicyRepresentationState for a site, loading from storage if needed."""
        scope_key = self.get_scope_key(site)
        with self._lock:
            if scope_key in self._site_states:
                raw_state = self._site_states[scope_key]
                try:
                    return PolicyRepresentationState(raw_state)
                except ValueError:
                    return PolicyRepresentationState.UNKNOWN

            # Attempt restore from persistent storage
            try:
                persisted = self.client.store.get_policy_utility(scope_key)
                if persisted is not None:
                    raw_state = persisted["state"]
                    self._site_states[scope_key] = raw_state
                    try:
                        return PolicyRepresentationState(raw_state)
                    except ValueError:
                        return PolicyRepresentationState.UNKNOWN
            except Exception as e:
                logger.debug("Failed to read persisted policy utility for %s: %s", site.name, e)

            # Default initial state
            initial_state = PolicyRepresentationState.UNKNOWN
            self._site_states[scope_key] = initial_state.value
            return initial_state

    def is_small_active(self, site: DecisionSite) -> bool:
        """Determines if Small representations should be extracted/used for this site."""
        state = self.get_state(site)
        if state in (PolicyRepresentationState.DISABLED, PolicyRepresentationState.DEGRADED):
            return False
        if state == PolicyRepresentationState.UNKNOWN:
            self.set_state(site, PolicyRepresentationState.EVALUATING)
        return True

    def record_small_inference(self, site: DecisionSite, elapsed_ms: float) -> None:
        """Records diagnostic metrics for a Small representation evaluation."""
        with self._lock:
            self.small_representation_calls += 1
            self.small_compute_ms += elapsed_ms

    def set_state(
        self,
        site: DecisionSite,
        state: PolicyRepresentationState | str,
        evidence: PolicyUtilityEvidence | None = None,
    ) -> None:
        """Sets and persists site policy representation state."""
        state_str = state.value if isinstance(state, PolicyRepresentationState) else str(state)
        scope_key = self.get_scope_key(site)
        checkpoint_rev = self._get_checkpoint_revision()

        with self._lock:
            self._site_states[scope_key] = state_str
            if evidence is not None:
                self._site_evidence[scope_key] = evidence

            try:
                ev_dict = canonical(evidence) if evidence else "{}"
                self.client.store.save_policy_utility(
                    site_key=scope_key,
                    site_version=site.version,
                    checkpoint_revision=checkpoint_rev,
                    state=state_str,
                    evidence=json.loads(ev_dict) if evidence else {},
                )
            except Exception as e:
                logger.debug("Failed to persist policy utility state for %s: %s", site.name, e)

    def evaluate_representation_utility(
        self,
        site: DecisionSite,
        *,
        classical_score: float,
        small_score: float,
        candidate_quality: float,
        candidate_lower_bound: float,
        accelerated_qualification: bool = False,
        coverage_expansion: float = 0.0,
    ) -> tuple[str, PolicyUtilityEvidence]:
        """Evaluates whether Small passes Condition A (Utility) and Condition B (Safety)."""
        error_budget = site.local_error_budget
        required_quality = (1.0 - error_budget) if error_budget is not None else 0.80

        # Condition A — Utility
        score_gain = small_score - classical_score
        condition_a_utility = (
            (score_gain >= 0.05)
            or accelerated_qualification
            or (coverage_expansion >= 0.05)
        )

        # Condition B — Safety against site error budget
        condition_b_point_safety = (candidate_quality >= required_quality)
        condition_b_statistical_safety = (candidate_lower_bound >= required_quality)

        if not condition_a_utility:
            decision = "DISABLED"
            target_state = PolicyRepresentationState.DISABLED
            reason = (
                f"Small representation produced no meaningful improvement over classical "
                f"compilation (Small: {small_score:.4f} vs Classical: {classical_score:.4f}, delta: {score_gain:+.4f})."
            )
        elif not condition_b_point_safety:
            decision = "DISABLED_FOR_SERVING_UTILITY"
            target_state = PolicyRepresentationState.DISABLED  # Shut off future inferences
            reason = (
                f"QUALITY_FAIL: Small representation improved selection score ({small_score:.4f} vs {classical_score:.4f}, delta: {score_gain:+.4f}), "
                f"but verified quality ({candidate_quality:.4f}) failed site error budget {error_budget} (required >= {required_quality:.4f}). "
                f"Serving authority denied."
            )
        elif not condition_b_statistical_safety:
            decision = "INSUFFICIENT_EVIDENCE"
            target_state = PolicyRepresentationState.EVALUATING  # Preserves candidate state so it can qualify later with more samples
            reason = (
                f"INSUFFICIENT_EVIDENCE: Small representation achieved point quality ({candidate_quality:.4f} >= {required_quality:.4f}), "
                f"but confidence lower bound ({candidate_lower_bound:.4f}) failed site error budget {error_budget} (required >= {required_quality:.4f}). "
                f"Serving authority denied; candidate preserved in EVALUATING for future epochs."
            )
        else:
            decision = "ENABLED"
            target_state = PolicyRepresentationState.ENABLED
            reason = (
                f"Small representation improved compilation ({small_score:.4f} vs {classical_score:.4f}) "
                f"and statistical lower bound ({candidate_lower_bound:.4f}) satisfied site error budget {error_budget}."
            )

        evidence = PolicyUtilityEvidence(
            site=site.name,
            site_version=site.version,
            checkpoint_revision=self._get_checkpoint_revision(),
            classical_score=classical_score,
            small_score=small_score,
            classical_qualified_coverage=0.0,
            small_qualified_coverage=0.0,
            classical_verified_quality=0.0,
            small_verified_quality=candidate_quality,
            host_calls_delta=0,
            compute_cost_seconds=round(self.small_compute_ms / 1000.0, 3),
            decision=decision,
            reason=reason,
        )

        self.set_state(site, target_state, evidence)
        return decision, evidence

    def degrade_site(self, site: DecisionSite, reason: str = "Semantic drift detected") -> None:
        """Transitions site state to DEGRADED on drift or schema changes to trigger reevaluation."""
        self.set_state(site, PolicyRepresentationState.DEGRADED)
        logger.info("Degraded policy utility for site %s: %s", site.name, reason)

    def invalidate_checkpoint(self) -> None:
        """Clears in-memory cache when model checkpoint changes."""
        with self._lock:
            self._site_states.clear()
            self._site_evidence.clear()

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Exposes runtime controller diagnostics."""
        with self._lock:
            enabled_cnt = sum(1 for s in self._site_states.values() if s == PolicyRepresentationState.ENABLED.value)
            disabled_cnt = sum(
                1 for s in self._site_states.values()
                if s in (PolicyRepresentationState.DISABLED.value, "DISABLED_FOR_SERVING_UTILITY")
            )
            return {
                "small_representation_calls": self.small_representation_calls,
                "small_enabled_sites": enabled_cnt,
                "small_disabled_sites": disabled_cnt,
                "small_compute_ms": round(self.small_compute_ms, 2),
            }
