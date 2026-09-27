"""Adapter capabilities and capability levels.

A runtime may support replanning but not switching models. The controller never
recommends an action the host cannot perform, so capabilities travel with the
recommendation.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .action import RuntimeAction

__all__ = ["Capabilities", "CapabilityLevel"]


@dataclass
class Capabilities:
    """What actuators an adapter can perform. Conservative by default."""

    replan: bool = True
    model_switch: bool = False
    context_compaction: bool = False
    checkpoint: bool = False
    retry_tool: bool = False
    branch_strategy: bool = False

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> Capabilities:
        if not payload:
            return cls()
        return cls(
            replan=bool(payload.get("replan", True)),
            model_switch=bool(payload.get("model_switch", False)),
            context_compaction=bool(payload.get("context_compaction", False)),
            checkpoint=bool(payload.get("checkpoint", False)),
            retry_tool=bool(payload.get("retry_tool", False)),
            branch_strategy=bool(payload.get("branch_strategy", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "replan": self.replan,
            "model_switch": self.model_switch,
            "context_compaction": self.context_compaction,
            "checkpoint": self.checkpoint,
            "retry_tool": self.retry_tool,
            "branch_strategy": self.branch_strategy,
        }

    def supports(self, action: str) -> bool:
        """Whether an adapter can perform ``action``.

        ``continue`` and ``stop`` are host-loop control, not actuators, so they
        are always available.
        """
        if action in (RuntimeAction.Continue, RuntimeAction.Stop):
            return True
        return {
            RuntimeAction.Replan: self.replan,
            RuntimeAction.EscalateModel: self.model_switch,
            RuntimeAction.DeescalateModel: self.model_switch,
            RuntimeAction.CompactContext: self.context_compaction,
            RuntimeAction.RestoreCheckpoint: self.checkpoint,
            RuntimeAction.RetryTool: self.retry_tool,
            RuntimeAction.BranchStrategy: self.branch_strategy,
        }.get(action, False)


class CapabilityLevel:
    """How much runtime data an integration exposes."""

    Signals = 0
    Progress = 1
    Runtime = 2
