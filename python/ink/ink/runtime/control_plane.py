"""Asynchronous Small policy-model observation path.

This module has no serving entry point.  Its outputs are durable compiler
features and receive no authority until verified outcomes qualify a compiled
Exact or Linear artifact.
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from ..internal.contracts import DecisionSite, digest
from ..internal.model.constants import INK_DECISION_LARGE

logger = logging.getLogger("ink.runtime.control_plane")


@dataclass(frozen=True)
class PolicyObservation:
    decision_id: str
    choice_scores: dict[str, float]
    representation: list[float] | None
    confidence: float
    ambiguity: float | None
    act_probability: float | None
    proposed_choice: str
    checkpoint_identity: str


class SmallControlPlane:
    """Runs Small after Host returns, outside synchronous request serving."""

    def __init__(self, client) -> None:
        self.client = client
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="InkSmallShadow")
        self._lock = threading.Lock()
        self._futures: set[Future] = set()
        self.inference_count = 0

    def submit(
        self,
        *,
        decision_id: str | None,
        site: DecisionSite,
        state: dict[str, Any],
        host_choice: str,
    ) -> Future | None:
        engine = self.client.engines.get("decision")
        backend = getattr(engine, "backend", None)
        if (
            not self.client._policy_shadow_enabled
            or decision_id is None
            or backend is None
            or backend.model_id == INK_DECISION_LARGE
            or not self.client.utility_controller.is_small_active(site)
        ):
            return None
        future = self._executor.submit(
            self._observe, decision_id, site, dict(state), host_choice, backend
        )
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(self._done)
        return future

    def _done(self, future: Future) -> None:
        with self._lock:
            self._futures.discard(future)
        try:
            future.result()
        except Exception as exc:  # Control-plane failure must never affect Host serving.
            logger.debug("Small shadow observation failed: %s", exc)

    def _observe(self, decision_id, site, state, host_choice, backend) -> PolicyObservation:
        import time

        proposal = None
        if hasattr(backend, "propose"):
            try:
                proposal = backend.propose(state, list(site.choices), site.instructions)
            except Exception as e:
                logger.debug("Policy proposal skipped: %s", e)

        t0 = time.perf_counter()
        try:
            representation = backend.representation(state, list(site.choices), site.instructions)
        except TypeError:  # Backward-compatible custom PolicyModelBackend.
            representation = backend.representation(state)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.client.utility_controller.record_small_inference(site, elapsed_ms)

        checkpoint_identity = digest(
            {
                "model_id": backend.model_id,
                "model_version": backend.model_version,
                "checkpoint_revision": backend.checkpoint_revision,
                "manifest_sha256": backend.manifest_sha256,
            }
        )
        choice = proposal.choice if proposal else (host_choice or (site.choices[0] if site.choices else ""))
        scores = dict(proposal.scores) if proposal else {c: (1.0 if c == choice else 0.0) for c in site.choices}
        confidence = float(proposal.confidence) if proposal else 1.0
        ambiguity = float(proposal.ambiguity) if (proposal and proposal.ambiguity is not None) else 0.0
        act_probability = (
            float(proposal.act_probability) if (proposal and proposal.act_probability is not None) else None
        )
        observation = PolicyObservation(
            decision_id=decision_id,
            choice_scores=scores,
            representation=list(representation) if representation is not None else None,
            confidence=confidence,
            ambiguity=ambiguity,
            act_probability=act_probability,
            proposed_choice=choice,
            checkpoint_identity=checkpoint_identity,
        )
        self.client.store.save_policy_observation(
            decision_id=decision_id,
            site_version=site.version,
            state=state,
            host_choice=host_choice,
            proposed_choice=observation.proposed_choice,
            scores=observation.choice_scores,
            representation=observation.representation,
            confidence=observation.confidence,
            ambiguity=observation.ambiguity,
            act_probability=observation.act_probability,
            checkpoint_identity=checkpoint_identity,
        )
        outcome = self.client.store.get_outcome(decision_id)
        if outcome:
            self.client.store.attach_policy_outcome(decision_id, json.loads(outcome["payload"]))
        with self._lock:
            self.inference_count += 1
        return observation

    def drain(self, timeout: float | None = None) -> None:
        with self._lock:
            futures = list(self._futures)
        for future in futures:
            future.result(timeout=timeout)

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)
