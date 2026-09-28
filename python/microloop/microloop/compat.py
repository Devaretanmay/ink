"""Explicit compatibility namespace for the pre-0.4 trajectory runtime.

New code should use the decision API (`Microloop`, `DecisionSite`, `decision`).
These names stay importable from the package root, but this module marks the
boundary: trajectory monitoring, progress states, interventions, policies, and
runtime controllers live here as compatibility surface.
"""

from .internal.legacy import *  # noqa: F403
from .internal.legacy import (  # noqa: F401
    SCHEMA_VERSION,
    __version__,
)
from .internal.legacy import __all__ as __all__
