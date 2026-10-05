"""Explicit bounded decisions and evidence-driven local fast paths."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import os
import secrets
import sqlite3
import tempfile
import threading
import time
import uuid
from collections import Counter
from dataclasses import asdict
from pathlib import Path

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

logger = logging.getLogger("ink")


def _migrate_legacy_default_database(path, *, readonly=False):
    """Move the default database to .ink with SQLite's consistent backup API.

    The old database and its WAL sidecars remain untouched as a recovery copy.
    """
    if str(path) != ".ink/decisions.db":
        return path
    target = Path(path)
    legacy = Path(".microloop/decisions.db")
    if target.exists() or not legacy.exists():
        return path
    if readonly:
        return str(legacy)

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, staging_name = tempfile.mkstemp(prefix=".decisions-migration-", dir=target.parent)
    os.close(fd)
    staging = Path(staging_name)
    try:
        source = sqlite3.connect(f"file:{legacy.resolve()}?mode=ro", uri=True)
        destination = sqlite3.connect(staging)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        os.replace(staging, target)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    return path

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
    eng_name = payload.get("engine_data", {}).get("engine", "exact")
    model_rev = (
        payload.get("engine_data", {}).get("revision")
        or payload.get("engine_data", {}).get("checkpoint")
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
        "model_revision": str(model_rev),
        "verifier": str(v_name),
        "verifier_version": str(v_ver),
        "requirements": req_data,
        "coverage_revision": cov_rev,
    }
    return digest(data), data


class MaintenanceOutcome(dict):
    def __getitem__(self, key):
        if key not in self:
            if key == "status":
                if "deferred" in self:
                    return "deferred"
                if "pending" in self:
                    val = str(self.get("pending", "")).lower()
                    if any(s in val for s in ("sqlite", "error", "unavailable", "locked")):
                        return "failed"
                    return "blocked"
                return "completed"
            if key == "reason":
                return self.get("deferred") or self.get("pending") or self.get("reason")
            if key == "action":
                if "compiled" in self:
                    return "compiled"
                if "demoted" in self:
                    return "demoted"
                return self.get("action")
        return super().__getitem__(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    @property
    def status(self):
        return self["status"]

    @property
    def reason(self):
        return self["reason"]


class Ink:

    def __init__(
        self,
        path=".ink/decisions.db",
        *,
        engines=(),
        readonly=False,
        timeout=2.0,
        auto_maintenance: bool = False,
        maintenance_interval: float = 30.0,
        maintenance_verifier=None,
        maintenance_requirements=None,
        maintenance_engine: str = "exact",
        disable_fast_path: bool = False,
        disabled: bool = False,
        model_enabled: bool = True,
        on_event=None,
    ):
        path = _migrate_legacy_default_database(path, readonly=readonly)
        self.store = DecisionStore(path, readonly=readonly, timeout=timeout)
        model_off = os.environ.get(
            "INK_MODEL_DISABLED", ""
        ).strip().lower() in ("1", "true", "yes")
        self._model_enabled = not model_off and model_enabled
        default_engines = [ExactEngine()]
        if self._model_enabled:
            default_engines.append(DecisionModelEngine())
        integral = {e.name: e for e in (*default_engines, *engines)}
        # Historical artifacts carry engine="laya"; resolve them to the integral engine.
        for alias, current in (("laya", "decision"), ("ink-decision-v1", "decision")):
            if alias not in integral and current in integral:
                integral[alias] = integral[current]
        self.engines = integral
        self._contracts = {}
        self._auto_maintenance = auto_maintenance
        self._maintenance_interval = float(maintenance_interval)
        self._maintenance_verifier = maintenance_verifier
        self._maintenance_requirements = maintenance_requirements
        self._maintenance_engine = resolve_engine_key(maintenance_engine)
        self._maintenance_visited: dict[str, float] = {}
        self._globally_disabled = disabled or os.environ.get(
            "INK_DISABLED", ""
        ).strip().lower() in ("1", "true", "yes")
        self._disable_fast_path = disable_fast_path or os.environ.get(
            "INK_DISABLE_FAST_PATH", ""
        ).strip().lower() in ("1", "true", "yes")
        self._on_event = on_event
        self._stop_event = threading.Event()

        self._maint_thread = None
        if auto_maintenance and not readonly and not self._globally_disabled:
            self._start_maintenance_thread()
        logger.debug(
            "Ink initialized: path=%s auto_maintenance=%s disabled=%s",
            path,
            auto_maintenance,
            self._globally_disabled,
        )

    def _emit_event(self, event: str, data: dict):
        if self._on_event is not None:
            try:
                self._on_event(event, data)
            except Exception:
                pass

    def _start_maintenance_thread(self):
        self._maint_thread = threading.Thread(
            target=self._maintenance_loop,
            name="InkMaintenance",
            daemon=True,
        )
        self._maint_thread.start()

    def _maintenance_loop(self):
        while not self._stop_event.is_set():
            if self._stop_event.wait(self._maintenance_interval):
                break
            try:
                self.maintenance(
                    verifier=self._maintenance_verifier,
                    requirements=self._maintenance_requirements,
                    engine=self._maintenance_engine,
                )
            except Exception:
                pass

    def close(self):
        if self._maint_thread is not None:
            self._stop_event.set()
            if self._maint_thread.is_alive():
                self._maint_thread.join(timeout=2.0)
            self._maint_thread = None
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
            verified_count = cov_row[0]["fast_count"] if cov_row and cov_row[0]["fast_count"] else 0
            min_samples = req.get("min_samples", 10)
            if verified_count >= 2 * min_samples:
                scale = math.sqrt((2 * min_samples) / verified_count)
                return max(min_floor, round(base_rate * scale, 4))
        return base_rate

    def _route(self, site, state):
        if self._disable_fast_path or self._globally_disabled:
            return None, None, "disabled_kill_switch"
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
                if payload["engine_data"].get("engine") == "exact" and canonical(
                    state
                ) not in payload["engine_data"].get("table", {}):
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
            eff_rate = self._effective_comparison_rate(artifact, profile)
            route_info["comparison_rate"] = eff_rate
            if secrets.randbelow(10**9) / 10**9 < eff_rate:
                return artifact, prediction, "comparison"
            return artifact, prediction, None
        except (sqlite3.Error, OSError, KeyError, ValueError, RuntimeError):
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
        if reason in ("storage_unavailable", "storage_locked"):
            logger.warning(
                "Storage contention/error for site %s (%s); failing open without durable record",
                site.name,
                reason,
            )
            self._emit_event("fail_open", {"site": site.name, "reason": reason})
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
            return self._save(site, state, value, task_id, started, artifact, prediction, reason)
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
        self._emit_event("fail_open", {"site": site.name, "reason": reason})
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

    def decide(self, *, site, state, fallback, choices=None, task_id=None, fallback_revision=None):
        started = time.perf_counter()
        if self._globally_disabled:
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
            self._emit_event(
                "decision",
                {
                    "site": str(site),
                    "choice": choice,
                    "source": "fallback",
                    "reason": "globally_disabled",
                },
            )
            return res
        site = self._site(site, state, choices, fallback_revision)
        state = site.encode(state)
        try:
            artifact, prediction, reason = self._route(site, state)
        except (sqlite3.Error, OSError, KeyError, ValueError, RuntimeError) as exc:
            logger.warning("Fast path routing error for site %s: %s; failing open", site.name, exc)
            artifact, prediction, reason = None, None, "engine_or_store_unavailable"
        if reason is None:
            try:
                res = self._save(
                    site, state, prediction["choice"], task_id, started, artifact, prediction, None
                )
                logger.debug(
                    "Decide: site=%s source=fast_path choice=%s latency=%.4fs",
                    site.name,
                    res.choice,
                    time.perf_counter() - started,
                )
                self._emit_event(
                    "decision",
                    {
                        "site": site.name,
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
        res = self._save_fallback(
            site, state, value, task_id, started, artifact, prediction, reason
        )
        logger.debug(
            "Decide: site=%s source=%s reason=%s choice=%s latency=%.4fs",
            site.name,
            res.source,
            res.fallback_reason,
            res.choice,
            time.perf_counter() - started,
        )
        self._emit_event(
            "decision",
            {
                "site": site.name,
                "decision_id": res.decision_id,
                "choice": res.choice,
                "source": res.source,
                "reason": res.fallback_reason,
            },
        )
        return res

    async def decide_async(
        self, *, site, state, fallback, choices=None, task_id=None, fallback_revision=None
    ):
        started = time.perf_counter()
        if self._globally_disabled:
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
            self._emit_event(
                "decision",
                {
                    "site": str(site),
                    "choice": choice,
                    "source": "fallback",
                    "reason": "globally_disabled",
                },
            )
            return res
        site = self._site(site, state, choices, fallback_revision)
        state = site.encode(state)
        try:
            artifact, prediction, reason = await asyncio.to_thread(self._route, site, state)
        except (sqlite3.Error, OSError, KeyError, ValueError, RuntimeError) as exc:
            logger.warning("Fast path routing error for site %s: %s; failing open", site.name, exc)
            artifact, prediction, reason = None, None, "engine_or_store_unavailable"
        if reason is None:
            try:
                res = self._save(
                    site, state, prediction["choice"], task_id, started, artifact, prediction, None
                )
                logger.debug(
                    "Decide: site=%s source=fast_path choice=%s latency=%.4fs",
                    site.name,
                    res.choice,
                    time.perf_counter() - started,
                )
                self._emit_event(
                    "decision",
                    {
                        "site": site.name,
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
        res = self._save_fallback(
            site, state, value, task_id, started, artifact, prediction, reason
        )
        logger.debug(
            "Decide: site=%s source=%s reason=%s choice=%s latency=%.4fs",
            site.name,
            res.source,
            res.fallback_reason,
            res.choice,
            time.perf_counter() - started,
        )
        self._emit_event(
            "decision",
            {
                "site": site.name,
                "decision_id": res.decision_id,
                "choice": res.choice,
                "source": res.source,
                "reason": res.fallback_reason,
            },
        )
        return res

    def record_outcome(
        self, decision_id, *, quality, verifier, verifier_version, evidence: dict | None = None
    ):
        if self._globally_disabled or decision_id is None:
            return None
        ev = evidence if evidence is not None else {}
        outcome = Outcome(quality, verifier, verifier_version, ev)
        payload = canonical(asdict(outcome))
        try:
            with self.store.transaction() as db:
                row = db.execute(
                    "SELECT site, state FROM decisions WHERE id=?", (decision_id,)
                ).fetchone()
                if not row:
                    return None
                existing = db.execute(
                    "SELECT payload FROM outcomes WHERE decision=?", (decision_id,)
                ).fetchone()
                if existing:
                    if existing[0] != payload:
                        raise ValueError("Conflicting outcome; recorded evidence is immutable")
                    return outcome
                db.execute(
                    "INSERT INTO outcomes VALUES (?,?,?)", (decision_id, payload, time.time())
                )
                db.execute(
                    """INSERT INTO state_coverage(site, state, observations, outcomes, quality_sum)
                    VALUES (?,?,0,1,?) ON CONFLICT(site, state) DO UPDATE SET
                    outcomes=outcomes+1, quality_sum=quality_sum+excluded.quality_sum""",
                    (row["site"], row["state"], outcome.quality),
                )
            logger.debug(
                "Recorded outcome: decision=%s quality=%s verifier=%s",
                decision_id,
                quality,
                verifier,
            )
            self._emit_event(
                "outcome",
                {"decision_id": decision_id, "quality": quality, "verifier": verifier},
            )
            return outcome
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return None

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
        logger.info(
            "Compiled candidate artifact %s for site %s (engine=%s)",
            artifact_id,
            site.version,
            engine,
        )
        self._emit_event(
            "lifecycle",
            {"site": site.name, "artifact": artifact_id, "action": "compile", "engine": engine},
        )
        return artifact_id

    def calibrate(self, site, *, verifier=None, requirements: PromotionRequirements | None = None):
        site = self._resolve(site)
        requirements = requirements or self._maintenance_requirements or DEFAULT_REQUIREMENTS
        artifact = self._artifact(site.version)
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
        rows = [r for r in self.store.history(site.version) if r["id"] in ids]
        records = verify_rows(
            rows,
            self.engines[eng_key],
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
        logger.info("Calibrated shadow candidate %s for site %s", artifact["id"], site.version)
        self._emit_event(
            "lifecycle",
            {"site": site.name, "artifact": artifact["id"], "action": "calibrate"},
        )
        return profile

    def evaluate(self, site, *, verifier=None, auto_promote: bool = True):
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
        eng_key = resolve_engine_key(payload["engine_data"]["engine"])
        if verifier is None and eng_key != "exact":
            raise ValueError(
                f"Semantic engines require a verifier ({eng_key}). "
                "Pass a callable verifier to evaluate() or maintenance()."
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
        ev_id, ev_id_data = compute_evidence_identity(
            site, payload, profile, verifier, requirements
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
                    logger.info(
                        "Promoted candidate %s to ACTIVE for site %s",
                        artifact["id"],
                        site.version,
                    )
                    self._emit_event(
                        "lifecycle",
                        {"site": site.name, "artifact": artifact["id"], "action": "promote"},
                    )
                else:
                    db.execute(
                        "UPDATE artifacts SET status='VERIFIED' WHERE id=?",
                        (artifact["id"],),
                    )
                    logger.info(
                        "Marked candidate %s as VERIFIED for site %s",
                        artifact["id"],
                        site.version,
                    )
                    self._emit_event(
                        "lifecycle",
                        {"site": site.name, "artifact": artifact["id"], "action": "verify"},
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
        stale_identity = False
        if artifact.get("evidence"):
            try:
                ev_raw = artifact["evidence"]
                ev = json.loads(ev_raw) if isinstance(ev_raw, str) else ev_raw
                ev_data = ev.get("evidence_identity_data")
                if ev_data:
                    if site.fallback_revision != ev_data.get("fallback_revision"):
                        stale_identity = True
                    if tuple(ev_data.get("choices", ())) != site.choices:
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
            except Exception:
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
            with self.store.transaction() as db:
                count = db.execute(
                    "UPDATE artifacts SET status='SHADOW',epoch=? "
                    "WHERE id=? AND status='ACTIVE' AND epoch=?",
                    (time.time(), artifact["id"], artifact["epoch"]),
                ).rowcount
                if count:
                    self._event(db, artifact["id"], "ACTIVE", "SHADOW", evidence)
                    logger.warning(
                        "Site %s demoted from ACTIVE to SHADOW (delta_lower=%s, quality_lower=%s)",
                        site.name,
                        delta_lower,
                        evidence["quality_lower"],
                    )
                    self._emit_event(
                        "lifecycle",
                        {
                            "site": site.name,
                            "artifact": artifact["id"],
                            "action": "demote",
                            "evidence": evidence,
                        },
                    )
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

    def invalidate(
        self,
        site: str | DecisionSite,
        *,
        reason: str = "policy_revision",
        action: str = "demote",
    ) -> dict:
        site = self._resolve(site)
        artifact = self._artifact(site.version)
        if artifact is None or artifact["status"] not in ("ACTIVE", "VERIFIED"):
            return {"site": site.name, "invalidated": False, "reason": "no_active_artifact"}
        target_status = "RETIRED" if action == "retire" else "SHADOW"
        now = time.time()
        with self.store.transaction() as db:
            db.execute(
                "UPDATE artifacts SET status=?, epoch=? WHERE id=?",
                (target_status, now, artifact["id"]),
            )
            self._event(
                db,
                artifact["id"],
                artifact["status"],
                target_status,
                {"reason": reason, "explicit": True},
            )
        logger.info(
            "Invalidated site %s: target=%s, reason=%s",
            site.name,
            target_status,
            reason,
        )
        self._emit_event(
            "lifecycle",
            {
                "site": site.name,
                "artifact": artifact["id"],
                "action": "invalidate",
                "target": target_status,
                "reason": reason,
            },
        )
        return {
            "site": site.name,
            "version": site.version,
            "artifact": artifact["id"],
            "previous_status": artifact["status"],
            "new_status": target_status,
            "reason": reason,
            "invalidated": True,
        }

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
        chosen_engine = (
            self._maintenance_engine
            if (getattr(self, "_maintenance_engine", None) and engine == "decision")
            else engine
        )
        engine = resolve_engine_key(chosen_engine)
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
            last_visit = self._maintenance_visited.get(site.version, 0.0)
            if st == "ACTIVE":
                return (0, last_drift, last_visit, -obs_count, site.name)
            elif st == "SHADOW":
                shadow_outcomes = sum(
                    1
                    for r in hist
                    if art
                    and r["created"] > art["epoch"]
                    and r["prediction"] is not None
                    and r["outcome"] is not None
                )
                return (1, -shadow_outcomes, last_visit, site.name)
            elif st == "CANDIDATE":
                return (2, -obs_count, last_visit, site.name)
            elif st == "OBSERVE":
                return (3, last_visit, -obs_count, site.name)
            else:
                return (4, last_visit, site.name)

        prioritized = sorted(resolved_sites, key=site_priority_key)
        start_time = time.perf_counter()
        total_rows = 0

        for site in prioritized:
            if max_sites is not None and len(results) >= max_sites:
                results[site.name] = MaintenanceOutcome({"deferred": "max_sites_reached"})
                continue
            if (
                time_budget_sec is not None
                and (time.perf_counter() - start_time) >= time_budget_sec
            ):
                results[site.name] = MaintenanceOutcome({"deferred": "time_budget_exceeded"})
                continue
            if max_rows is not None and total_rows >= max_rows:
                results[site.name] = MaintenanceOutcome({"deferred": "max_rows_reached"})
                continue

            self._maintenance_visited[site.version] = time.time()
            site_rows = len(self.store.history(site.version))
            total_rows += site_rows
            try:
                artifact = self._artifact(site.version)
                req = requirements or self._maintenance_requirements or DEFAULT_REQUIREMENTS
                ver = verifier if verifier is not None else self._maintenance_verifier
                if artifact is None:
                    blocker = self._compile_blocker(site, req)
                    if blocker is not None:
                        results[site.name] = MaintenanceOutcome({"pending": blocker})
                        continue
                    artifact_id = self.compile(site, engine=engine)
                    results[site.name] = MaintenanceOutcome({"compiled": artifact_id})
                    artifact = self._artifact(site.version)
                    if ver is not None or engine == "exact":
                        try:
                            self.calibrate(site, verifier=ver, requirements=req)
                        except ValueError:
                            pass
                elif artifact["status"] == "ACTIVE":
                    reeval = self.reevaluate(site)
                    results[site.name] = MaintenanceOutcome(reeval)
                    cur = self._artifact(site.version)


                    if cur and cur["status"] == "ACTIVE" and cur.get("profile"):
                        prof = cur["profile"]
                        cov_data = prof.get("coverage_engine")
                        req_dict = prof.get("requirements", {})
                        if cov_data:
                            cov_engine = CoverageEngine.from_dict(cov_data)
                            history = self.store.history(site.version)
                            recent_counterexamples = [
                                r
                                for r in history[-50:]
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
                                            db,
                                            cur["id"],
                                            "ACTIVE",
                                            "ACTIVE",
                                            {"event": "region_tightened", "regions": tightened},
                                        )

                            if req_dict.get("allow_region_split", True) and not req_dict.get(
                                "high_risk", False
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
                                prof.pop("id", None)
                                prof["id"] = digest(prof)
                                with self.store.transaction() as db:
                                    db.execute(
                                        "UPDATE artifacts SET profile=? WHERE id=?",
                                        (canonical(prof), cur["id"]),
                                    )
                                results[site.name]["self_tuned"] = True
                else:
                    eng = resolve_engine_key(artifact["payload"]["engine_data"]["engine"])
                    if ver is None and eng != "exact":
                        results[site.name] = MaintenanceOutcome({"pending": "verifier_required"})
                        continue
                    if artifact["profile"] is None:
                        try:
                            self.calibrate(site, verifier=ver, requirements=req)
                            artifact = self._artifact(site.version)
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
                            artifact = self._artifact(site.version)
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
            except (ValueError, KeyError, sqlite3.Error) as error:
                results[site.name] = MaintenanceOutcome({"pending": str(error)})


        return results

    def _compile_blocker(self, site, requirements=None):
        site = self._resolve(site)
        req = requirements or self._maintenance_requirements or DEFAULT_REQUIREMENTS
        rows = self.store.history(site.version)
        if not rows:
            return "waiting_for_observations"
        eligible = [r for r in rows if r["source"] == "fallback" and r["outcome"] is not None]
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

    def _require_compilation_support(self, site, requirements):
        blocker = self._compile_blocker(site, requirements)
        if blocker == "impossible_bounds":
            raise ValueError("Finite samples cannot meet the configured confidence bounds")
        if blocker is not None:
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
        blocker = "none"
        if artifact is None:
            b = self._compile_blocker(site)
            blocker = b if b is not None else "ready_to_compile"
        elif artifact["status"] == "SHADOW":
            if artifact["profile"] is None:
                eng = resolve_engine_key(artifact["payload"]["engine_data"]["engine"])
                if eng != "exact" and self._maintenance_verifier is None:
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
            "name": site.name,
            "version": site.version,
            "contract": asdict(site),
            "model": {
                "enabled": self._model_enabled,
                "loaded": bool(
                    self.engines.get("decision")
                    and getattr(self.engines["decision"], "_agents", {})
                ),
                "implementation": (
                    self.engines["decision"].name if self.engines.get("decision") else None
                ),
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
            "auto_maintenance": self._auto_maintenance,
        }

    def status(self, site: str | DecisionSite) -> dict:
        insp = self.inspect(site)
        return {
            "name": insp["name"],
            "version": insp["version"],
            "model_enabled": self._model_enabled,
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
            "auto_maintenance": self._auto_maintenance,
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
        site=None,
        *,
        keep_recent: int = 1000,
        before_timestamp: float | None = None,
        max_age_days: float | None = None,
        vacuum: bool = False,
    ) -> dict:
        if max_age_days is not None:
            before_timestamp = time.time() - (max_age_days * 86400.0)
        if site is None:
            all_sites = [
                DecisionSite(**json.loads(r["contract"]))
                for r in self.store.rows("SELECT contract FROM sites ORDER BY name,created")
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
                with self.store.lock:
                    self.store.conn.execute("VACUUM")
            return {"site": "all", "pruned": pruned_total, "remaining": remaining_total}

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

        logger.info(
            "Compacted site %s: pruned %d, remaining %d",
            site.name,
            len(rows_to_prune),
            remaining,
        )
        self._emit_event(
            "lifecycle",
            {
                "site": site.name,
                "action": "compact",
                "pruned": len(rows_to_prune),
                "remaining": remaining,
            },
        )
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
        disagreements = [r for r in comparisons if r.get("outcome") and r["choice"] != reg.choice]
        disagreement_rate = len(disagreements) / len(comparisons) if comparisons else 0.0

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
        return [self.region_health(site, reg.region_id) for reg in cov_engine.semantic_regions]


_default_client = None


def decision(*, site, state, choices=None, fallback, **kwargs):
    global _default_client
    if _default_client is None:
        _default_client = Ink()
    return _default_client.decide(
        site=site, state=state, choices=choices, fallback=fallback, **kwargs
    )


def record_outcome(decision_id, **kwargs):
    if _default_client is None:
        raise ValueError("No default client has recorded a decision")
    return _default_client.record_outcome(decision_id, **kwargs)
