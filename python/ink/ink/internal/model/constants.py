"""Canonical internal model identifiers, versions, and resolvers."""

from __future__ import annotations

import warnings

INK_DECISION_SMALL = "ink-decision-small"
INK_DECISION_LARGE = "ink-decision-large"

LEGACY_INK_DECISION_V1 = "ink-decision-v1"
LEGACY_DECISION_V1 = "decision-v1"

MODEL_RUNTIME_VERSION = "2"

CANONICAL_MODEL_IDS = (INK_DECISION_SMALL, INK_DECISION_LARGE)
ALL_POLICY_MODEL_IDS = (INK_DECISION_SMALL, INK_DECISION_LARGE, LEGACY_INK_DECISION_V1, LEGACY_DECISION_V1)

_WARNED_LEGACY_ALIASES: set[str] = set()


def reset_deprecation_warnings() -> None:
    """Clear warned legacy aliases cache (primarily for tests)."""
    _WARNED_LEGACY_ALIASES.clear()


def resolve_policy_model_id(name: str | None) -> str | None:
    """Resolve a user-facing or legacy model name into a canonical PolicyModel ID.

    Mapping:
      - None / "disabled" -> None
      - "small" -> "ink-decision-small"
      - "large" -> "ink-decision-large"
      - "auto"  -> "auto"
      - "ink-decision-small" -> "ink-decision-small"
      - "ink-decision-large" -> "ink-decision-large"
      - "ink-decision-v1" / "decision-v1" -> "ink-decision-small" with DeprecationWarning (warn once)
    """
    if name is None:
        return None
    normalized = str(name).strip().lower()
    if normalized in ("", "none", "disabled", "false", "0"):
        return None
    if normalized in ("small", INK_DECISION_SMALL):
        return INK_DECISION_SMALL
    if normalized in ("large", INK_DECISION_LARGE):
        return INK_DECISION_LARGE
    if normalized == "auto":
        return "auto"
    if normalized in (LEGACY_INK_DECISION_V1, LEGACY_DECISION_V1):
        if normalized not in _WARNED_LEGACY_ALIASES:
            warnings.warn(
                f'"{name}" is deprecated. Use "{INK_DECISION_SMALL}".',
                DeprecationWarning,
                stacklevel=2,
            )
            _WARNED_LEGACY_ALIASES.add(normalized)
        return INK_DECISION_SMALL
    raise ValueError(
        f"Unknown policy model '{name}'. Expected 'small', 'large', 'auto', or 'disabled'."
    )
