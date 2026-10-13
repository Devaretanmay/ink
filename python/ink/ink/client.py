"""Public developer facade and orchestration for Ink Behavior JIT."""

from __future__ import annotations

import inspect
import json
import logging
import math
import os
import sqlite3
import tempfile
import threading
import time
import warnings
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .internal.contracts import (
    DecisionResult,
    DecisionSite,
    Outcome,
    PromotionRequirements,
    canonical,
)
from .internal.decision_store import DecisionStore
from .internal.engines import (
    ExactEngine,
    LinearClassifierEngine,
    PolicyModelEngine,
    resolve_engine_key,
)
from .runtime.control_plane import SmallControlPlane
from .runtime.diagnostics import Diagnostics
from .runtime.dispatcher import Dispatcher
from .runtime.epoch_manager import EpochManager
from .runtime.evaluator import Evaluator, MaintenanceOutcome
from .runtime.utility_controller import PolicyUtilityController

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


def _extract_decision_state(args: tuple, kwargs: dict) -> dict[str, Any]:
    """Extracts dictionary decision state from positional or keyword args."""
    if args and isinstance(args[0], dict):
        return args[0]
    return dict(kwargs)


class Ink:
    """Public facade for Ink's Behavior JIT runtime."""

    def __init__(
        self,
        path: str | Path = ".ink/decisions.db",
        *,
        engines=(),
        readonly: bool = False,
        timeout: float = 2.0,
        auto_maintenance: bool = False,
        maintenance_interval: float = 30.0,
        maintenance_verifier=None,
        maintenance_requirements: PromotionRequirements | None = None,
        maintenance_engine: str = "exact",
        disable_fast_path: bool = False,
        disabled: bool = False,
        model_enabled: bool = True,
        policy_model: str | None = "small",
        policy_shadow: bool = False,
        on_event: Callable[[str, dict], None] | None = None,
        app_id: str = "default",
        namespace: str | None = None,
    ) -> None:
        path = _migrate_legacy_default_database(path, readonly=readonly)
        self.app_id = str(namespace or app_id or "default")
        self.store = DecisionStore(path, readonly=readonly, timeout=timeout)

        from .internal.model.constants import (
            INK_DECISION_SMALL,
            LEGACY_DECISION_V1,
            LEGACY_INK_DECISION_V1,
            resolve_policy_model_id,
        )

        env_policy = os.environ.get("INK_POLICY_MODEL")
        if env_policy is not None:
            active_policy = env_policy
        elif os.environ.get("INK_MODEL_DISABLED", "").strip().lower() in ("1", "true", "yes"):
            warnings.warn(
                "INK_MODEL_DISABLED is deprecated. Use INK_POLICY_MODEL=disabled.",
                DeprecationWarning,
                stacklevel=2,
            )
            active_policy = "disabled"
        else:
            active_policy = policy_model if model_enabled else "disabled"

        resolved_policy = resolve_policy_model_id(active_policy)
        self.policy_model = resolved_policy
        self._model_enabled = resolved_policy is not None
        self._policy_shadow_enabled = bool(policy_shadow and self._model_enabled)

        default_engines = [ExactEngine(), LinearClassifierEngine()]
        if self._model_enabled and resolved_policy:
            default_engines.append(PolicyModelEngine(model=resolved_policy))
        integral = {e.name: e for e in (*default_engines, *engines)}
        if "decision" in integral:
            integral[INK_DECISION_SMALL] = integral["decision"]
            integral[LEGACY_INK_DECISION_V1] = integral["decision"]
            integral[LEGACY_DECISION_V1] = integral["decision"]
        for alias, current in (
            ("classifier", "linear"),
            ("logistic", "linear"),
        ):
            if alias not in integral and current in integral:
                integral[alias] = integral[current]
        self.engines = integral
        self._contracts: dict[str, DecisionSite] = {}
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

        # Initialize subcomponents
        self._dispatcher = Dispatcher(self)
        self._evaluator = Evaluator(self)
        self._compiler = self._evaluator.compiler
        self._qualification = self._evaluator.qualification
        self._epoch_manager = EpochManager(self.store, app_id=self.app_id)
        self.utility_controller = PolicyUtilityController(self)
        self._utility_controller = self.utility_controller
        self._diagnostics = Diagnostics(self)
        self._control_plane = SmallControlPlane(self)

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
        self._control_plane.close()
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def register(self, site: DecisionSite) -> DecisionSite:
        contract = canonical(asdict(site))
        self.store.save_site(site.version, site.name, contract, time.time())
        self._contracts[site.name] = site
        return site

    def _resolve(self, site: str | DecisionSite) -> DecisionSite:
        if isinstance(site, DecisionSite):
            return site
        contract = self.store.get_site_contract(site)
        if not contract:
            raise KeyError(f"Unknown decision site: {site}")
        return DecisionSite(**json.loads(contract))

    # Internal compatibility hooks accessed directly by tests and submodules
    def _site(self, site, state, choices, fallback_revision):
        return self._dispatcher.resolve_site(site, state, choices, fallback_revision)

    def _artifact(self, site_version: str):
        return self._dispatcher.load_artifact(site_version)

    def _effective_comparison_rate(self, artifact, profile) -> float:
        return self._dispatcher.effective_comparison_rate(artifact, profile)

    def _route(self, site, state):
        return self._dispatcher.route(site, state)

    def _save(self, site, state, value, task_id, started, artifact, prediction, reason):
        return self._dispatcher.save(
            site, state, value, task_id, started, artifact, prediction, reason
        )

    def _save_fallback(self, site, state, value, task_id, started, artifact, prediction, reason):
        return self._dispatcher.save_fallback(
            site, state, value, task_id, started, artifact, prediction, reason
        )

    def _event(self, db, artifact, previous, current, detail):
        self.store.record_event(artifact, previous, current, canonical(detail), db=db)

    # Core Decision Execution API
    def decide(
        self,
        site: str | DecisionSite | None = None,
        state: dict[str, Any] | None = None,
        *,
        fallback: Callable[[], Any],
        choices: tuple[str, ...] | list[str] | None = None,
        task_id: str | None = None,
        fallback_revision: str | None = None,
        **kwargs,
    ) -> DecisionResult:
        if site is None:
            site = kwargs.pop("site", None)
        if state is None:
            state = kwargs.pop("state", None)
        if site is None or state is None:
            raise ValueError("Both 'site' and 'state' must be provided to decide()")
        return self._dispatcher.decide(
            site=site,
            state=state,
            fallback=fallback,
            choices=choices,
            task_id=task_id,
            fallback_revision=fallback_revision,
        )

    async def decide_async(
        self,
        site: str | DecisionSite | None = None,
        state: dict[str, Any] | None = None,
        *,
        fallback: Callable[[], Any],
        choices: tuple[str, ...] | list[str] | None = None,
        task_id: str | None = None,
        fallback_revision: str | None = None,
        **kwargs,
    ) -> DecisionResult:
        if site is None:
            site = kwargs.pop("site", None)
        if state is None:
            state = kwargs.pop("state", None)
        if site is None or state is None:
            raise ValueError("Both 'site' and 'state' must be provided to decide_async()")
        return await self._dispatcher.decide_async(
            site=site,
            state=state,
            fallback=fallback,
            choices=choices,
            task_id=task_id,
            fallback_revision=fallback_revision,
        )

    def record_outcome(
        self,
        decision_id: str | None,
        outcome: Outcome | None = None,
        *,
        quality: float | None = None,
        verifier: str | None = None,
        verifier_version: str | None = None,
        evidence: dict | None = None,
    ) -> Outcome | None:
        if self._globally_disabled or decision_id is None:
            return None
        if outcome is not None and isinstance(outcome, Outcome):
            q = outcome.quality
            v = outcome.verifier
            vv = outcome.verifier_version
            ev = outcome.evidence
        else:
            q = quality
            v = verifier
            vv = verifier_version
            ev = evidence if evidence is not None else {}
        outcome_obj = Outcome(q, v, vv, ev)
        payload = canonical(asdict(outcome_obj))
        try:
            row = self.store.record_outcome_transaction(decision_id, payload, outcome_obj.quality)
            if not row:
                return None
            self.store.attach_policy_outcome(decision_id, asdict(outcome_obj))
            if row and row["artifact"]:
                try:
                    artifact_id = row["artifact"]
                    pred_data = json.loads(row["prediction"]) if row["prediction"] else {}
                    route_info = pred_data.get("route_info", {})
                    region_id = route_info.get("semantic_region") or row["state"]
                    if region_id:
                        epoch_row = self.store.get_open_epoch(artifact_id, self.app_id)
                        if epoch_row:
                            epoch_id = epoch_row["epoch_id"]
                            req_data = json.loads(epoch_row["requirements"])
                            min_region_samples = req_data.get("min_region_samples", 5)
                            min_conf = req_data.get("min_confidence", 0.70)

                            pred_choice = pred_data.get("choice") or row["choice"]
                            expected = ev.get("expected")
                            is_match = int(
                                outcome_obj.quality >= 0.8
                                and (not expected or expected == pred_choice)
                            )
                            self._epoch_manager.record_progressive_evidence(
                                epoch_id=epoch_id,
                                region_id=region_id,
                                decision_id=decision_id,
                                state_canonical=row["state"],
                                choice=pred_choice,
                                expected_choice=expected,
                                quality=outcome_obj.quality,
                                verified_match=is_match,
                                distance=route_info.get("distance"),
                            )

                            reg_ev = self._epoch_manager.get_progressive_evidence(epoch_id, region_id)
                            n_samples = len(reg_ev)
                            n_pos = sum(r["verified_match"] for r in reg_ev)
                            n_neg = n_samples - n_pos
                            p_hat = n_pos / max(n_samples, 1)
                            eps = math.sqrt(math.log(20) / (2 * max(n_samples, 1)))
                            q_lower = max(0.0, p_hat - eps)

                            reg_status = "SHADOW"
                            if n_samples >= min_region_samples and q_lower >= min_conf and n_neg == 0:
                                reg_status = "ACTIVE"

                            self._epoch_manager.sync_region_lifecycle(
                                artifact_id=artifact_id,
                                site_version=row["site"],
                                region_id=region_id,
                                status=reg_status,
                                radius=route_info.get("radius") or 0.5,
                                negative_margin=route_info.get("negative_margin") or 0.8,
                                sample_count=n_samples,
                                verified_positive=n_pos,
                                verified_negative=n_neg,
                                quality_lower=round(q_lower, 4),
                            )
                except Exception as ex:
                    logger.debug("Progressive evidence error: %s", ex)
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
            return outcome_obj
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return None

    def drain_control_plane(self, timeout: float | None = None) -> None:
        """Wait for queued Small shadow observations; intended for tests and shutdowns."""
        self._control_plane.drain(timeout=timeout)

    @property
    def policy_inference_count(self) -> int:
        """Count completed Small control-plane inferences, never serving calls."""
        return self._control_plane.inference_count

    # Lifecycle, Qualification & Maintenance Delegation
    def compile(self, site, *, engine="auto", replace_existing=False) -> str:
        return self._compiler.compile(site, engine=engine, replace_existing=replace_existing)

    def calibrate(self, site, *, verifier=None, requirements: PromotionRequirements | None = None) -> dict:
        return self._qualification.calibrate(site, verifier=verifier, requirements=requirements)

    def evaluate(self, site, *, verifier=None, auto_promote: bool = True) -> dict:
        return self._qualification.evaluate(site, verifier=verifier, auto_promote=auto_promote)

    def reevaluate(self, site) -> dict:
        return self._qualification.reevaluate(site)

    def invalidate(self, site: str | DecisionSite, *, reason: str = "policy_revision", action: str = "demote") -> dict:
        return self._evaluator.invalidate(site, reason=reason, action=action)

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
        return self._evaluator.maintenance(
            sites=sites,
            max_sites=max_sites,
            max_rows=max_rows,
            time_budget_sec=time_budget_sec,
            verifier=verifier,
            requirements=requirements,
            engine=engine,
        )

    def hot_swap(self, site, new_artifact_id: str) -> str:
        return self._evaluator.hot_swap(site, new_artifact_id)

    def compact(
        self,
        site=None,
        *,
        keep_recent: int = 1000,
        before_timestamp: float | None = None,
        max_age_days: float | None = None,
        vacuum: bool = False,
    ) -> dict:
        return self._evaluator.compact(
            site=site,
            keep_recent=keep_recent,
            before_timestamp=before_timestamp,
            max_age_days=max_age_days,
            vacuum=vacuum,
        )

    # Diagnostics & Telemetry Delegation
    def sites(self) -> list[dict[str, Any]]:
        return self._diagnostics.sites()

    def profile(self, site) -> dict[str, Any]:
        return self._diagnostics.profile(site)

    def coverage(self, site) -> list[dict[str, Any]]:
        return self._diagnostics.coverage(site)

    def promotions(self, site) -> list[dict[str, Any]]:
        return self._diagnostics.promotions(site)

    def drift_history(self, site) -> list[dict[str, Any]]:
        return self._diagnostics.drift_history(site)

    def lineage(self, site) -> list[dict[str, Any]]:
        return self._diagnostics.lineage(site)

    def receipt(self, decision_id: str) -> dict[str, Any]:
        return self._diagnostics.receipt(decision_id)

    def inspect(self, site) -> dict[str, Any]:
        return self._diagnostics.inspect(site)

    def status(self, site: str | DecisionSite) -> dict[str, Any]:
        return self._diagnostics.status(site)

    def health(self, site: str | DecisionSite) -> dict[str, Any]:
        return self._diagnostics.health(site)

    def fleet_health(self) -> dict[str, Any]:
        return self._diagnostics.fleet_health()

    def region_health(self, site, region_id: str) -> dict[str, Any]:
        return self._diagnostics.region_health(site, region_id)

    def all_region_health(self, site) -> list[dict[str, Any]]:
        return self._diagnostics.all_region_health(site)

    def utility_diagnostics(self) -> dict[str, Any]:
        """Exposes runtime Small policy model utility gating diagnostics."""
        return self._diagnostics.utility()

    def wrap(
        self,
        site: str | DecisionSite,
        *,
        choices: tuple[str, ...] | list[str] | None = None,
        fallback_revision: str | None = None,
        unpack: bool = False,
        task_id: str | None = None,
    ):
        """Decorator wrapping a decision function with Ink's Fast Path runtime."""
        def decorator(fn):
            if inspect.iscoroutinefunction(fn):
                async def async_wrapper(*args, **kwargs):
                    state = _extract_decision_state(args, kwargs)
                    async def async_fallback():
                        return await fn(*args, **kwargs)
                    res = await self.decide_async(
                        site=site,
                        state=state,
                        choices=choices,
                        fallback=async_fallback,
                        task_id=task_id,
                        fallback_revision=fallback_revision,
                    )
                    return res.choice if unpack else res
                async_wrapper.__wrapped__ = fn
                async_wrapper.__name__ = getattr(fn, "__name__", "wrapped_decision")
                async_wrapper.__doc__ = getattr(fn, "__doc__", None)
                return async_wrapper
            else:
                def sync_wrapper(*args, **kwargs):
                    state = _extract_decision_state(args, kwargs)
                    res = self.decide(
                        site=site,
                        state=state,
                        choices=choices,
                        fallback=lambda: fn(*args, **kwargs),
                        task_id=task_id,
                        fallback_revision=fallback_revision,
                    )
                    return res.choice if unpack else res
                sync_wrapper.__wrapped__ = fn
                sync_wrapper.__name__ = getattr(fn, "__name__", "wrapped_decision")
                sync_wrapper.__doc__ = getattr(fn, "__doc__", None)
                return sync_wrapper
        return decorator


_default_client: Ink | None = None


def decide(*args, **kwargs):
    global _default_client
    if _default_client is None:
        _default_client = Ink()
    return _default_client.decide(*args, **kwargs)


decision = decide


def record_outcome(decision_id, **kwargs):
    if _default_client is None:
        raise ValueError("No default client has recorded a decision")
    return _default_client.record_outcome(decision_id, **kwargs)


def wrap(site, *, client=None, **kwargs):
    """Module-level decorator wrapping a decision function with Ink."""
    global _default_client
    if client is None:
        if _default_client is None:
            _default_client = Ink()
        client = _default_client
    return client.wrap(site, **kwargs)
