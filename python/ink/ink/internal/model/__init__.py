"""Ink Policy Model inference runtime and version metadata."""

from __future__ import annotations

import platform
from importlib.metadata import PackageNotFoundError, version

from .constants import (
    INK_DECISION_LARGE,
    INK_DECISION_SMALL,
    LEGACY_DECISION_V1,
    LEGACY_INK_DECISION_V1,
    MODEL_RUNTIME_VERSION,
    resolve_policy_model_id,
)


def _version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        # Construction still works without an available engine: fallback owns execution.
        return "unavailable"


# Runtime format version is explicitly decoupled from model branding/IDs.
RUNTIME_VERSION = ";".join(
    [
        f"ink-runtime-v{MODEL_RUNTIME_VERSION}",
        "precision=float16",
        *(f"{name}={_version(name)}" for name in ("mlx", "numpy", "tokenizers")),
        f"{platform.system()}-{platform.machine()}",
    ]
)

__all__ = [
    "INK_DECISION_SMALL",
    "INK_DECISION_LARGE",
    "LEGACY_INK_DECISION_V1",
    "LEGACY_DECISION_V1",
    "MODEL_RUNTIME_VERSION",
    "RUNTIME_VERSION",
    "resolve_policy_model_id",
]
