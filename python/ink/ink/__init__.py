"""Ink turns proven, verified decisions into local Fast Paths.

Models handle novelty; Ink turns proven behavior into software. Candidate
generation is separated from serving authority: a decision is served locally
only after independent outcome evidence qualifies it.

Decision API: `Ink`, `DecisionSite`, `DecisionResult`, `FallbackResult`,
`Outcome`, `PromotionRequirements`, `decision`, `record_outcome`, `wrap`.
Exceptions: `InkError`, `ContractError`, `ArtifactError`, `StorageError`, `ConfigurationError`.
"""

import logging
from importlib.metadata import version as _version

from .decision_api import DEFAULT_REQUIREMENTS, Ink, decide, decision, record_outcome, wrap
from .discovery import CandidateSite, discover_from_file, discover_from_traces
from .errors import (
    ArtifactError,
    ConfigurationError,
    ContractError,
    InkError,
    StorageError,
)
from .internal.contracts import (
    DecisionResult,
    DecisionSite,
    FallbackResult,
    Outcome,
    PromotionRequirements,
)
from .internal.engines import PolicyModelEngine, PolicyProposal
from .internal.model.constants import (
    INK_DECISION_LARGE,
    INK_DECISION_SMALL,
    LEGACY_INK_DECISION_V1,
)

logging.getLogger("ink").addHandler(logging.NullHandler())

try:
    __version__: str = _version("ink-jit")
except Exception:
    try:
        __version__ = _version("ink")
    except Exception:
        __version__ = "0.6.1"

__all__ = [
    "Ink",
    "DecisionSite",
    "DecisionResult",
    "FallbackResult",
    "Outcome",
    "PromotionRequirements",
    "DEFAULT_REQUIREMENTS",
    "decide",
    "decision",
    "record_outcome",
    "wrap",
    "CandidateSite",
    "discover_from_file",
    "discover_from_traces",
    "InkError",
    "ContractError",
    "ArtifactError",
    "StorageError",
    "ConfigurationError",
    "INK_DECISION_SMALL",
    "INK_DECISION_LARGE",
    "LEGACY_INK_DECISION_V1",
    "PolicyModelEngine",
    "PolicyProposal",
    "__version__",
]

