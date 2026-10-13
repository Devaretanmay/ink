"""Decision dispatch, fast-path execution, and fallback coordination."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import secrets
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from ..internal.contracts import (
    DecisionResult,
    DecisionSite,
    FallbackResult,
    canonical,
    digest,
)
from ..internal.coverage import CoverageEngine
from ..internal.engines import resolve_engine_key

if TYPE_CHECKING:
    from ..client import Ink

logger = logging.getLogger("ink.runtime.dispatcher")


class Dispatcher:
    """Coordinates runtime routing, local fast-path execution, and host fallback."""

    def __init__(self, client: Ink) -> None:
        self.client = client

    @property
    def store(self):
        return self.client.store

    @property
    def engines(self):
        return self.client.engines

    def resolve_site(
        self,
        site: str | DecisionSite,
        state: dict[str, Any],
        choices: tuple[str, ...] | list[str] | None,
        fallback_revision: str | None,
    ) -> DecisionSite:
        """Resolves or infers a DecisionSite contract and registers it if new."""
        if isinstance(site, DecisionSite):
            if choices is not None and tuple(choices) != site.choices:
                raise ValueError("Choices disagree with registered contract")
            try:
                return self.client.register(site)
            except sqlite3.Error:
                # Explicit contracts still allow the original agent to operate during an outage.
                return site
        try:
            existing = self.store.get_site_contracts(site)
        except sqlite3.Error:
            known = self.client._contracts.get(site)
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
            return self.resolve_site(DecisionSite(**contract), state, None, None)
        if choices is None:
            raise ValueError(
                f"Site {site!r} is not registered yet. "
                "Provide 'choices' on first call or pass an explicit DecisionSite."
            )
        types = {str: "string", int: "integer", float: "number", bool: "boolean"}
        try:
            schema = {key: types[type(value)] for key, value in state.items()}
        except KeyError as error:
            bad = [k for k, v in state.items() if type(v) not in types]
            raise ValueError(
                f"Unsupported state field types in {bad}. Inferred schema only supports primitive "
                "types (str, int, float, bool). Flatten complex structures or define DecisionSite."
            ) from error
        return self.resolve_site(
            DecisionSite(site, schema, tuple(choices), fallback_revision or "1"),
            state,
            None,
            None,
        )

    def load_artifact(self, site_version: str) -> dict[str, Any] | None:
        """Loads and verifies the active or shadow artifact for the site."""
        row = self.store.get_artifact_for_site(site_version)
        if not row:
            return None
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

    def load_artifacts(self, site_version: str) -> list[dict[str, Any]]:
        """Load verified live serving artifacts in Exact -> Linear order."""
        loaded = []
        for row in self.store.get_artifacts_for_site(site_version):
            row["payload"] = json.loads(row["payload"])
            engine_name = resolve_engine_key(row["payload"].get("engine_data", {}).get("engine", ""))
            if engine_name not in ("exact", "linear"):
                continue
            if digest(row["payload"]) != row["checksum"]:
                raise ValueError("Artifact integrity mismatch")
            row["profile"] = json.loads(row["profile"]) if row["profile"] else None
            if row["profile"]:
                profile = dict(row["profile"])
                profile_hash = profile.pop("id")
                if digest(profile) != profile_hash:
                    raise ValueError("Profile integrity mismatch")
            loaded.append(row)
        loaded.sort(
            key=lambda item: (
                0 if resolve_engine_key(item["payload"]["engine_data"]["engine"]) == "exact" else 1,
                -item["epoch"],
            )
        )
        return loaded

    def effective_comparison_rate(
        self, artifact: dict[str, Any] | None, profile: dict[str, Any] | None
    ) -> float:
        """Computes current comparison sample rate with safety bounds and adaptive scaling."""
        if not profile:
            return 0.25
        req = profile.get("requirements", {})
        base_rate = req.get("comparison_rate", 0.25)
        if not req.get("allow_adaptive_comparison", True) or req.get("high_risk", False):
            return base_rate
        min_floor = req.get("min_comparison_rate", 0.05)
        if artifact and artifact.get("id"):
            d = self.store.get_latest_drift_check(artifact["id"])
            if d:
                if d.get("demoted") or (d.get("delta_lower") is not None and d["delta_lower"] < 0):
                    return min(0.50, max(base_rate * 1.5, 0.25))
                if d.get("missing_outcomes", 0) > 0:
                    return min(0.50, max(base_rate * 1.5, 0.25))

            verified_count = self.store.get_total_fast_served(artifact["site"])
            min_samples = req.get("min_samples", 10)
            if verified_count >= 2 * min_samples:
                scale = math.sqrt((2 * min_samples) / verified_count)
                return max(min_floor, round(base_rate * scale, 4))
        return base_rate

    def route(
        self, site: DecisionSite, state: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
        """Serve strictly through qualified Exact, then qualified Linear, then Host."""
        if self.client._disable_fast_path or self.client._globally_disabled:
            return None, None, "disabled_kill_switch"
        try:
            artifacts = self.load_artifacts(site.version)
        except (sqlite3.Error, OSError, KeyError, ValueError, RuntimeError):
            return None, None, "engine_or_store_unavailable"
        if not artifacts:
            return None, None, "observe"

        shadow_result = None
        last_reason = "outside_coverage"
        for artifact in artifacts:
            routed = self._route_artifact(site, state, artifact)
            _, prediction, reason = routed
            if reason is None or reason == "comparison":
                return routed
            if reason == "shadow" and shadow_result is None:
                shadow_result = routed
            last_reason = reason or last_reason
        return shadow_result or (artifacts[-1], None, last_reason)

    def _route_artifact(
        self, site: DecisionSite, state: dict[str, Any], artifact: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
        """Evaluate one serving artifact. Never evaluates a Policy Model."""
        try:
            payload = artifact["payload"]
            engine_name = resolve_engine_key(payload["engine_data"]["engine"])
            if engine_name not in ("exact", "linear"):
                return artifact, None, "non_serving_engine"
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
                if artifact.get("id"):
                    active_from_lc = self.store.get_active_regions(artifact["id"])
                    if active_from_lc and artifact["status"] == "ACTIVE":
                        for r in cov_engine.semantic_regions:
                            if r.region_id in active_from_lc:
                                r.status = "ACTIVE"
                level, reg, conf = cov_engine.route(state)
                if level == "exact":
                    region = reg
                elif level == "semantic":
                    region = reg
                elif level == "shadow":
                    region = reg
                    is_semantic_shadow = True
                elif level == "ambiguous":
                    return artifact, None, "ambiguous"
                elif level == "revoked":
                    return artifact, None, "revoked"
                else:
                    return artifact, None, "outside_coverage"
            else:
                region = coverage.get(canonical(state))
                if region is None:
                    return artifact, None, "outside_coverage"

            engine = self.engines[engine_name]
            state_in = state
            has_proto = region and "prototype_state" in region
            if (is_semantic_shadow or level == "semantic") and has_proto:
                engine_data = payload["engine_data"]
                uses_compiled_small = engine_data.get("representation_strategy") in (
                    "small", "structured_small"
                )
                if (
                    engine_data.get("engine") == "exact"
                    and canonical(state) not in engine_data.get("table", {})
                ) or uses_compiled_small:
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
                "producer": payload["engine_data"].get("engine", "exact"),
                "choice": choice,
                "raw_probability": probability,
                "raw_confidence": region.get("confidence", 0.0),
                "confidence": region.get("confidence", 0.0),
                "model_revision": (
                    payload["engine_data"].get("revision")
                    or payload["engine_data"].get("checkpoint")
                    or payload["engine_data"].get("engine")
                ),
                "artifact_id": artifact["id"],
                "route_info": route_info,
            }
            if artifact.get("evidence"):
                try:
                    ev_raw = artifact["evidence"]
                    ev = json.loads(ev_raw) if isinstance(ev_raw, str) else ev_raw
                    ev_data = ev.get("evidence_identity_data")
                    if ev_data:
                        if site.fallback_revision != ev_data.get("fallback_revision"):
                            return artifact, prediction, "stale_evidence_identity"
                        if tuple(ev_data.get("choices", ())) != site.choices:
                            return artifact, prediction, "stale_evidence_identity"
                        eng_key = resolve_engine_key(payload["engine_data"]["engine"])
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
                                return artifact, prediction, "stale_evidence_identity"
                except Exception:
                    pass

            if artifact["status"] != "ACTIVE" or profile is None or is_semantic_shadow:
                return artifact, prediction, "shadow"
            if (
                choice != region.get("choice")
                or prediction["confidence"] < profile["requirements"]["min_confidence"]
            ):
                return artifact, prediction, "insufficient_confidence"
            eff_rate = self.effective_comparison_rate(artifact, profile)
            route_info["comparison_rate"] = eff_rate
            if secrets.randbelow(10**9) / 10**9 < eff_rate:
                return artifact, prediction, "comparison"
            return artifact, prediction, None
        except (sqlite3.Error, OSError, KeyError, ValueError, RuntimeError):
            return artifact, None, "engine_or_store_unavailable"

    def save(
        self,
        site: DecisionSite,
        state: dict[str, Any],
        value: Any,
        task_id: str | None,
        started: float,
        artifact: dict[str, Any] | None,
        prediction: dict[str, Any] | None,
        reason: str | None,
    ) -> DecisionResult:
        """Persists executed decision transaction and updates coverage telemetry."""
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
            "engine": prediction.get("producer") if source == "fast_path" and prediction else None,
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
        if source == "fast_path":
            self.store.commit_fast_path_decision(
                decision_id=result.decision_id,
                site_version=site.version,
                task_id=task_id or result.decision_id,
                created=time.time(),
                state_json=canonical(state),
                choice=result.choice,
                artifact_id=artifact["id"],
                fast_path_version=result.fast_path_version,
                prediction_json=canonical(prediction) if prediction else None,
                confidence=result.confidence,
                elapsed=time.perf_counter() - started,
                usage_json=canonical(usage),
                expected_epoch=artifact["epoch"],
            )
        else:
            self.store.commit_fallback_decision(
                decision_id=result.decision_id,
                site_version=site.version,
                task_id=task_id or result.decision_id,
                created=time.time(),
                state_json=canonical(state),
                choice=result.choice,
                reason=reason,
                artifact_id=result.fast_path_version,
                fast_path_version=result.fast_path_version,
                prediction_json=canonical(prediction) if prediction else None,
                confidence=result.confidence,
                elapsed=time.perf_counter() - started,
                usage_json=canonical(usage),
            )
        return result

    def save_fallback(
        self,
        site: DecisionSite,
        state: dict[str, Any],
        value: Any,
        task_id: str | None,
        started: float,
        artifact: dict[str, Any] | None,
        prediction: dict[str, Any] | None,
        reason: str | None,
    ) -> DecisionResult:
        """Handles fail-open semantics when storage is contended or unavailable."""
        if reason in ("storage_unavailable", "storage_locked"):
            logger.warning(
                "Storage contention/error for site %s (%s); failing open without durable record",
                site.name,
                reason,
            )
            self.client._emit_event("fail_open", {"site": site.name, "reason": reason})
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
                None,
                "fallback",
                site.version,
                fallback_reason=reason,
                recorded=False,
                receipt=receipt,
            )
        try:
            save_fn = getattr(self.client, "_save", self.save)
            return save_fn(site, state, value, task_id, started, artifact, prediction, reason)
        except sqlite3.OperationalError as exc:
            reason = (
                "storage_locked"
                if ("locked" in str(exc) or "busy" in str(exc))
                else "storage_unavailable"
            )
        except sqlite3.Error:
            reason = "storage_unavailable"

        logger.warning(
            "Storage contention/error for site %s (%s); failing open without durable record",
            site.name,
            reason,
        )
        self.client._emit_event("fail_open", {"site": site.name, "reason": reason})
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
            None,
            "fallback",
            site.version,
            fallback_reason=reason,
            recorded=False,
            receipt=receipt,
        )

    def decide(
        self,
        *,
        site: str | DecisionSite,
        state: dict[str, Any],
        fallback: Callable[[], Any],
        choices: tuple[str, ...] | list[str] | None = None,
        task_id: str | None = None,
        fallback_revision: str | None = None,
    ) -> DecisionResult:
        """Synchronously routes and serves a decision."""
        started = time.perf_counter()
        if self.client._globally_disabled:
            val = fallback()
            if inspect.isawaitable(val):
                if inspect.iscoroutine(val):
                    val.close()
                raise TypeError("Async fallbacks require decide_async")
            choice = val.choice if isinstance(val, FallbackResult) else val
            s_ver = site.version if isinstance(site, DecisionSite) else str(site)
            res = DecisionResult(
                choice,
                None,
                "fallback",
                s_ver,
                fallback_reason="globally_disabled",
                recorded=False,
            )
            self.client._emit_event(
                "decision",
                {
                    "site": str(site),
                    "choice": choice,
                    "source": "fallback",
                    "reason": "globally_disabled",
                },
            )
            return res

        resolved_site = self.resolve_site(site, state, choices, fallback_revision)
        encoded_state = resolved_site.encode(state)

        # Route via client._route to preserve monkeypatching in existing test suites
        route_fn = getattr(self.client, "_route", self.route)
        try:
            artifact, prediction, reason = route_fn(resolved_site, encoded_state)
        except (sqlite3.Error, OSError, KeyError, ValueError, RuntimeError) as exc:
            logger.warning("Fast path routing error for site %s: %s; failing open", resolved_site.name, exc)
            artifact, prediction, reason = None, None, "engine_or_store_unavailable"

        save_fn = getattr(self.client, "_save", self.save)
        save_fallback_fn = getattr(self.client, "_save_fallback", self.save_fallback)

        if reason is None:
            try:
                res = save_fn(
                    resolved_site,
                    encoded_state,
                    prediction["choice"],
                    task_id,
                    started,
                    artifact,
                    prediction,
                    None,
                )
                logger.debug(
                    "Decide: site=%s source=fast_path choice=%s latency=%.4fs",
                    resolved_site.name,
                    res.choice,
                    time.perf_counter() - started,
                )
                self.client._emit_event(
                    "decision",
                    {
                        "site": resolved_site.name,
                        "decision_id": res.decision_id,
                        "choice": res.choice,
                        "source": "fast_path",
                        "reason": None,
                    },
                )
                return res
            except sqlite3.OperationalError as exc:
                reason = (
                    "storage_locked"
                    if ("locked" in str(exc) or "busy" in str(exc))
                    else "storage_unavailable"
                )
            except sqlite3.Error:
                reason = "storage_unavailable"

        value = fallback()
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            raise TypeError("Async fallbacks require decide_async")

        res = save_fallback_fn(
            resolved_site, encoded_state, value, task_id, started, artifact, prediction, reason
        )
        if res.recorded:
            self.client._control_plane.submit(
                decision_id=res.decision_id,
                site=resolved_site,
                state=encoded_state,
                host_choice=res.choice,
            )
        logger.debug(
            "Decide: site=%s source=%s reason=%s choice=%s latency=%.4fs",
            resolved_site.name,
            res.source,
            res.fallback_reason,
            res.choice,
            time.perf_counter() - started,
        )
        self.client._emit_event(
            "decision",
            {
                "site": resolved_site.name,
                "decision_id": res.decision_id,
                "choice": res.choice,
                "source": res.source,
                "reason": res.fallback_reason,
            },
        )
        return res

    async def decide_async(
        self,
        *,
        site: str | DecisionSite,
        state: dict[str, Any],
        fallback: Callable[[], Any],
        choices: tuple[str, ...] | list[str] | None = None,
        task_id: str | None = None,
        fallback_revision: str | None = None,
    ) -> DecisionResult:
        """Asynchronously routes and serves a decision without blocking the event loop."""
        started = time.perf_counter()
        if self.client._globally_disabled:
            if inspect.iscoroutinefunction(fallback):
                val = await fallback()
            else:
                val = fallback()
                if inspect.isawaitable(val):
                    val = await val
            choice = val.choice if isinstance(val, FallbackResult) else val
            s_ver = site.version if isinstance(site, DecisionSite) else str(site)
            res = DecisionResult(
                choice,
                None,
                "fallback",
                s_ver,
                fallback_reason="globally_disabled",
                recorded=False,
            )
            self.client._emit_event(
                "decision",
                {
                    "site": str(site),
                    "choice": choice,
                    "source": "fallback",
                    "reason": "globally_disabled",
                },
            )
            return res

        resolved_site = self.resolve_site(site, state, choices, fallback_revision)
        encoded_state = resolved_site.encode(state)

        # Offload route check to thread to avoid blocking loop
        route_fn = getattr(self.client, "_route", self.route)
        try:
            artifact, prediction, reason = await asyncio.to_thread(
                route_fn, resolved_site, encoded_state
            )
        except (sqlite3.Error, OSError, KeyError, ValueError, RuntimeError) as exc:
            logger.warning("Fast path routing error for site %s: %s; failing open", resolved_site.name, exc)
            artifact, prediction, reason = None, None, "engine_or_store_unavailable"

        save_fn = getattr(self.client, "_save", self.save)
        save_fallback_fn = getattr(self.client, "_save_fallback", self.save_fallback)

        if reason is None:
            try:
                res = save_fn(
                    resolved_site,
                    encoded_state,
                    prediction["choice"],
                    task_id,
                    started,
                    artifact,
                    prediction,
                    None,
                )
                logger.debug(
                    "Decide: site=%s source=fast_path choice=%s latency=%.4fs",
                    resolved_site.name,
                    res.choice,
                    time.perf_counter() - started,
                )
                self.client._emit_event(
                    "decision",
                    {
                        "site": resolved_site.name,
                        "decision_id": res.decision_id,
                        "choice": res.choice,
                        "source": "fast_path",
                        "reason": None,
                    },
                )
                return res
            except sqlite3.OperationalError as exc:
                reason = (
                    "storage_locked"
                    if ("locked" in str(exc) or "busy" in str(exc))
                    else "storage_unavailable"
                )
            except sqlite3.Error:
                reason = "storage_unavailable"

        if inspect.iscoroutinefunction(fallback):
            value = await fallback()
        else:
            value = fallback()
            if inspect.isawaitable(value):
                value = await value

        res = save_fallback_fn(
            resolved_site, encoded_state, value, task_id, started, artifact, prediction, reason
        )
        if res.recorded:
            self.client._control_plane.submit(
                decision_id=res.decision_id,
                site=resolved_site,
                state=encoded_state,
                host_choice=res.choice,
            )
        logger.debug(
            "Decide: site=%s source=%s reason=%s choice=%s latency=%.4fs",
            resolved_site.name,
            res.source,
            res.fallback_reason,
            res.choice,
            time.perf_counter() - started,
        )
        self.client._emit_event(
            "decision",
            {
                "site": resolved_site.name,
                "decision_id": res.decision_id,
                "choice": res.choice,
                "source": res.source,
                "reason": res.fallback_reason,
            },
        )
        return res
