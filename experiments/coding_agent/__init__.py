"""Real coding-agent experiment harness."""

from .core import (
    CLASSIFICATION_SITE,
    InkInstrumentor,
    ToolObservation,
    Verification,
    normalize_state,
    verify_tool_result,
)
from .opencode import ExperimentConfig, TaskSpec, run_experiment

__all__ = [
    "CLASSIFICATION_SITE",
    "ExperimentConfig",
    "InkInstrumentor",
    "TaskSpec",
    "ToolObservation",
    "Verification",
    "normalize_state",
    "run_experiment",
    "verify_tool_result",
]
