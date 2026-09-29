"""Microloop turns repeated agent decisions into verified fast paths.

Decision API: `Microloop`, `DecisionSite`, `DecisionResult`, `FallbackResult`,
`Outcome`, `PromotionRequirements`, `decision`, `record_outcome`.
"""
from .decision_api import Microloop, decision, record_outcome
from .internal.contracts import (
    DecisionResult,
    DecisionSite,
    FallbackResult,
    Outcome,
    PromotionRequirements,
)
from .microloop_core import version as _version

__version__: str = _version()

__all__ = ["Microloop", "DecisionSite", "DecisionResult", "FallbackResult",
           "Outcome", "PromotionRequirements", "decision", "record_outcome",
           "__version__"]
