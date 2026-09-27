"""Model tiers: a provider-neutral ladder for model escalation.

Microloop never names a model. The controller recommends a *direction*
(``escalate_model`` / ``deescalate_model``) and the adapter owns the concrete
ladder. A ``ModelTier`` is the adapter-side name for a rung, so integrations can
map ``fast`` / ``balanced`` / ``strong`` onto whatever model ids the provider
currently sells -- which change faster than the controller should care about.
"""
from __future__ import annotations

__all__ = ["ModelTier"]

#: Ordered weakest to strongest. The order is the escalation ladder.
_ORDER = ("fast", "balanced", "strong")


class ModelTier:
    """The three rungs an adapter can map onto concrete model ids."""

    Fast = "fast"
    Balanced = "balanced"
    Strong = "strong"

    #: Ordered weakest to strongest.
    Order: tuple[str, ...] = _ORDER

    @classmethod
    def rank(cls, tier: str | None) -> int:
        """Position of ``tier`` on the ladder; ``-1`` when unknown."""
        return _ORDER.index(tier) if tier in _ORDER else -1

    @classmethod
    def is_tier(cls, value: object) -> bool:
        return value in _ORDER

    @classmethod
    def up(cls, tier: str | None) -> str | None:
        """The next stronger tier, or ``None`` at the top."""
        rank = cls.rank(tier)
        if rank < 0 or rank + 1 >= len(_ORDER):
            return None
        return _ORDER[rank + 1]

    @classmethod
    def down(cls, tier: str | None) -> str | None:
        """The next weaker tier, or ``None`` at the bottom."""
        rank = cls.rank(tier)
        if rank <= 0:
            return None
        return _ORDER[rank - 1]
