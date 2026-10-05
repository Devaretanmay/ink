"""Ink turns repeated agent decisions into verified fast paths.

Decision API: `Ink`, `DecisionSite`, `DecisionResult`, `FallbackResult`,
`Outcome`, `PromotionRequirements`, `decision`, `record_outcome`.
"""

import logging
from importlib.metadata import version as _version

from .decision_api import Ink, decision, record_outcome
from .discovery import CandidateSite, discover_from_file, discover_from_traces
from .internal.contracts import (
    DecisionResult,
    DecisionSite,
    FallbackResult,
    Outcome,
    PromotionRequirements,
)

logging.getLogger("ink").addHandler(logging.NullHandler())

try:
    __version__: str = _version("ink")
except Exception:
    __version__ = "0.6.0rc2"

__all__ = [
    "Ink",
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
