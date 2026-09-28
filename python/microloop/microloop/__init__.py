"""Microloop turns repeated agent decisions into verified fast paths.

Decision API: `Microloop`, `DecisionSite`, `DecisionResult`, `FallbackResult`,
`Outcome`, `PromotionRequirements`, `decision`, `record_outcome`.

Compatibility: pre-0.4 trajectory names stay importable here and under
`microloop.compat`. New integrations should use the decision API.
"""
from . import compat  # noqa: F401
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

__all__ = ["Microloop", "DecisionSite", "DecisionResult", "FallbackResult",
           "Outcome", "PromotionRequirements", "decision", "record_outcome",
           "compat", *_legacy_all]
