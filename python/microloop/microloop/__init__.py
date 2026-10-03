"""Microloop turns repeated agent decisions into verified fast paths.

Decision API: `Microloop`, `DecisionSite`, `DecisionResult`, `FallbackResult`,
`Outcome`, `PromotionRequirements`, `decision`, `record_outcome`.
"""

import logging
from importlib.metadata import version as _version

from .decision_api import Microloop, decision, record_outcome
from .discovery import CandidateSite, discover_from_file, discover_from_traces
from .internal.contracts import (
    DecisionResult,
    DecisionSite,
    FallbackResult,
    Outcome,
    PromotionRequirements,
)

logging.getLogger("microloop").addHandler(logging.NullHandler())

try:
    __version__: str = _version("microloop")
except Exception:
    __version__ = "0.6.0rc1"

__all__ = [
    "Microloop",
    "DecisionSite",
    "DecisionResult",
    "FallbackResult",
    "Outcome",
    "PromotionRequirements",
    "decision",
    "record_outcome",
    "CandidateSite",
    "discover_from_file",
    "discover_from_traces",
    "__version__",
]
