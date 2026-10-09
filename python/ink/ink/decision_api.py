"""Backward-compatible re-export module for Ink decision primitives and API.

In Ink 0.6.0+, the monolithic decision API was structurally decomposed into:
- ink.client: Public facade and lifecycle orchestration
- ink.runtime.dispatcher: Fast-path routing and fallback dispatching
- ink.runtime.evaluator: Compilation, calibration, and qualification
- ink.runtime.epoch_manager: Progressive evidence epochs and region lifecycles
- ink.runtime.diagnostics: Inspection, status, health formatting, and receipts
- ink.internal.decision_store: Transactional SQLite storage encapsulation

This module preserves 100% backward compatibility for all existing imports.
"""

from __future__ import annotations

from .client import (
    Ink,
    _migrate_legacy_default_database,
    decide,
    decision,
    record_outcome,
    wrap,
)
from .internal.contracts import (
    DecisionResult,
    DecisionSite,
    FallbackResult,
    Outcome,
    PromotionRequirements,
    canonical,
    digest,
)
from .internal.decision_store import split_history
from .internal.engines import resolve_engine_key
from .runtime.evaluator import (
    DEFAULT_REQUIREMENTS,
    MaintenanceOutcome,
    compute_evidence_identity,
)

__all__ = [
    "Ink",
    "DecisionSite",
    "DecisionResult",
    "FallbackResult",
    "Outcome",
    "PromotionRequirements",
    "DEFAULT_REQUIREMENTS",
    "MaintenanceOutcome",
    "decide",
    "decision",
    "record_outcome",
    "wrap",
    "canonical",
    "digest",
    "split_history",
    "resolve_engine_key",
    "compute_evidence_identity",
    "_migrate_legacy_default_database",
]
