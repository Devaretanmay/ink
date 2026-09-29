"""Microloop Decision v1: integral vendored inference runtime (Linux/macOS via MLX)."""

import platform
from importlib.metadata import PackageNotFoundError, version


def _version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        # Construction still works without an available engine: fallback owns execution.
        return "unavailable"


# Numeric and tokenization changes require qualification on the actual runtime.
RUNTIME_VERSION = ";".join(
    [
        "microloop-decision-v1.0",
        "precision=float16",
        *(f"{name}={_version(name)}" for name in ("mlx", "numpy", "tokenizers")),
        f"{platform.system()}-{platform.machine()}",
    ]
)
