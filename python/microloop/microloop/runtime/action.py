"""Runtime action vocabulary.

The full set is defined now so later passes add actuators without an interface
change. Only ``continue``, ``replan`` and ``stop`` are enabled in this pass; the
rest are experimental and are never recommended yet.
"""
from __future__ import annotations

__all__ = ["RuntimeAction"]


class RuntimeAction:
    """What the runtime might change about a running execution."""

    Continue = "continue"
    Replan = "replan"
    EscalateModel = "escalate_model"
    DeescalateModel = "deescalate_model"
    CompactContext = "compact_context"
    RestoreCheckpoint = "restore_checkpoint"
    RetryTool = "retry_tool"
    BranchStrategy = "branch_strategy"
    Stop = "stop"

    #: Actions this release is allowed to recommend. Pass 2 added the three
    #: actuators; checkpointing, tool retry and branching remain unproduced.
    _ENABLED = frozenset(
        {Continue, Replan, EscalateModel, DeescalateModel, CompactContext, Stop}
    )

    @classmethod
    def is_enabled(cls, action: str) -> bool:
        return action in cls._ENABLED

    @classmethod
    def is_experimental(cls, action: str) -> bool:
        return action not in cls._ENABLED

    @classmethod
    def is_actuator(cls, action: str) -> bool:
        """True when the action changes execution and an adapter must perform it."""
        return action not in (cls.Continue, cls.Stop)
