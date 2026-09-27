"""Action outcomes: whether an adaptation actually helped.

"Improved" means progress improved after the action, not that the action caused
success. Keeping that distinction is what makes the eventual learning loop
honest.
"""
from __future__ import annotations

__all__ = ["ActionOutcome", "outcome_for", "progress_rank"]

_RANK = {"healthy": 0, "warning": 1, "stalled": 2, "regressing": 3}


class ActionOutcome:
    """How an attempted action turned out."""

    Pending = "pending"
    Improved = "improved"
    NoChange = "no_change"
    Regressed = "regressed"


def progress_rank(state: str) -> int:
    """Categorical ordering of a progress state; lower is better."""
    return _RANK.get(state, 0)


def outcome_for(before: str, after: str) -> str:
    """Judge an adaptation from the progress before and after it."""
    if progress_rank(after) < progress_rank(before):
        return ActionOutcome.Improved
    if progress_rank(after) > progress_rank(before):
        return ActionOutcome.Regressed
    return ActionOutcome.NoChange
