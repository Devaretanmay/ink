"""Microloop turns repeated agent decisions into verified fast paths."""
from .decision_api import Microloop, decision, record_outcome
from .internal.contracts import (
    DecisionResult,
    DecisionSite,
    FallbackResult,
    Outcome,
    PromotionRequirements,
)
from .internal.legacy import *  # noqa: F403
from .internal.legacy import SCHEMA_VERSION, __version__  # noqa: F401
from .internal.legacy import __all__ as _legacy_all

__all__ = [*_legacy_all, "Microloop", "DecisionSite", "DecisionResult", "FallbackResult",
           "Outcome", "PromotionRequirements", "decision", "record_outcome"]
