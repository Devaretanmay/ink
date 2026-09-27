"""The adapter protocol integrations implement to expose runtime capabilities.

Microloop contains no framework-specific logic. An integration reports what it
can do (:meth:`RuntimeAdapter.capabilities`), what the runtime looks like now
(:meth:`RuntimeAdapter.snapshot`), whether a given action is performable at this
moment (:meth:`RuntimeAdapter.can_apply`), and how to perform it
(:meth:`RuntimeAdapter.apply`).

The adapter owns the concrete model ladder and the compaction implementation:
Microloop only ever recommends a *direction* such as ``escalate_model`` and never
names a model.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .action import RuntimeAction
from .capabilities import Capabilities
from .compaction import CompactionReport, ContextCompactor, Segment
from .state import RuntimeState, Usage
from .tier import ModelTier

__all__ = [
    "DEFAULT_REPLAN_MESSAGE",
    "AgentRuntimeAdapter",
    "ApplyResult",
    "MockAdapter",
    "RuntimeAdapter",
    "TieredAdapter",
]

#: Injected into the agent's context on a replan. Deliberately short: it asks the
#: agent to re-evaluate from the current state rather than repeating itself.
DEFAULT_REPLAN_MESSAGE = (
    "Your current approach is no longer making progress.\n"
    "Re-evaluate the task from the current workspace state.\n"
    "Do not repeat the previous strategy.\n"
    "Use the latest verification results to choose a different approach."
)


@dataclass
class ApplyResult:
    """What an adapter did with a recommended action."""

    applied: bool
    action: str
    reason: str | None = None
    runtime: RuntimeState | None = None

    def to_dict(self):
        return {
            "applied": self.applied,
            "action": self.action,
            "reason": self.reason,
            "runtime": self.runtime.to_dict() if self.runtime is not None else None,
        }


@runtime_checkable
class RuntimeAdapter(Protocol):
    """Standard way for an integration to expose runtime capabilities."""

    def capabilities(self) -> Capabilities:
        """What this runtime could change."""

    def snapshot(self) -> RuntimeState:
        """The current runtime state, or an empty state when unknown."""

    def can_apply(self, action: str) -> bool:
        """Whether ``action`` can be performed right now.

        A runtime at the top of its model ladder returns ``False`` for
        ``escalate_model`` without Microloop knowing anything about models.
        """

    def apply(self, action: str) -> ApplyResult:
        """Perform a runtime action and report what happened."""


class MockAdapter:
    """A deterministic adapter for tests and local experiments."""

    def __init__(
        self,
        capabilities: Capabilities | None = None,
        snapshot: RuntimeState | None = None,
        unavailable: set[str] | None = None,
    ) -> None:
        self._capabilities = capabilities or Capabilities()
        self._snapshot = snapshot or RuntimeState()
        self._unavailable = set(unavailable or ())
        self.applied: list[str] = []

    def capabilities(self) -> Capabilities:
        return self._capabilities

    def snapshot(self) -> RuntimeState:
        return self._snapshot

    def can_apply(self, action: str) -> bool:
        return self._capabilities.supports(action) and action not in self._unavailable

    def apply(self, action: str) -> ApplyResult:
        self.applied.append(action)
        return ApplyResult(applied=True, action=action, runtime=self._snapshot)


@runtime_checkable
class AgentRuntimeAdapter(RuntimeAdapter, Protocol):
    """A :class:`RuntimeAdapter` that also owns a model tier ladder.

    Integrations implement this so a host can map provider-neutral tiers onto
    concrete model ids, and so the controller can reason about escalation as a
    direction without ever learning a model name.
    """

    def current_tier(self) -> str | None:
        """The tier in use now, or ``None`` when the host has no ladder."""

    def tiers(self) -> list[str]:
        """The tiers available, weakest to strongest."""


class TieredAdapter:
    """A real, reusable adapter: tier switching, replan injection, compaction.

    Concrete integrations subclass this and supply the tier-to-model-id map, the
    context limit and (optionally) a :class:`ContextCompactor`. The harness owns
    the actual model calls; this adapter owns the runtime state those calls run
    under, which is what Microloop observes and actuates.
    """

    def __init__(
        self,
        tiers: Mapping[str, str],
        *,
        start: str | None = None,
        context_limit: int | None = None,
        capabilities: Capabilities | None = None,
        compactor: ContextCompactor | None = None,
        replan_message: str = DEFAULT_REPLAN_MESSAGE,
        transcript: list[Segment] | None = None,
    ) -> None:
        if not tiers:
            raise ValueError("tiers must map at least one model tier to a model id")
        order = [tier for tier in ModelTier.Order if tier in tiers]
        if not order:
            raise ValueError("tiers must use ModelTier names (fast/balanced/strong)")
        self._tiers = dict(tiers)
        self._order = order
        default = ModelTier.Balanced if ModelTier.Balanced in order else order[0]
        self._index = order.index(start) if start in order else order.index(default)
        self.context_limit = context_limit
        self._compactor = compactor
        self._replan_message = replan_message
        self._capabilities = capabilities or Capabilities(
            model_switch=len(order) > 1,
            context_compaction=compactor is not None,
        )
        self.transcript: list[Segment] = list(transcript or [])
        self.replans = 0
        self.compactions = 0
        self.last_compaction: CompactionReport | None = None
        self.pending_replan: str | None = None
        self.usage = Usage()
        self.tool_calls = 0
        self._elapsed = 0.0

    # -- ladder ---------------------------------------------------------------
    @property
    def tier(self) -> str:
        return self._order[self._index]

    @property
    def model(self) -> str:
        return self._tiers[self.tier]

    def tiers(self) -> list[str]:
        return list(self._order)

    def current_tier(self) -> str:
        return self.tier

    def capabilities(self) -> Capabilities:
        return self._capabilities

    # -- host-reported bookkeeping -------------------------------------------
    def record_segment(
        self, kind: str, text: str, *, step: int | None = None, **meta: Any
    ) -> Segment:
        segment = Segment(kind=kind, text=text, step=step, meta=dict(meta))
        self.transcript.append(segment)
        return segment

    def add_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost: float = 0.0,
        elapsed_seconds: float = 0.0,
    ) -> None:
        self.usage = Usage(
            cost=(self.usage.cost or 0.0) + cost,
            input_tokens=(self.usage.input_tokens or 0) + input_tokens,
            output_tokens=(self.usage.output_tokens or 0) + output_tokens,
        )
        self._elapsed += elapsed_seconds

    def add_tool_call(self) -> None:
        self.tool_calls += 1

    def context_tokens(self) -> int:
        """A deterministic estimate: one token per ~4 characters of transcript."""
        return sum(len(segment.text) for segment in self.transcript) // 4

    # -- adapter protocol -----------------------------------------------------
    def snapshot(self) -> RuntimeState:
        return RuntimeState(
            model=self.model,
            context_tokens=self.context_tokens(),
            context_limit=self.context_limit,
            input_tokens=self.usage.input_tokens,
            output_tokens=self.usage.output_tokens,
            cost=self.usage.cost,
            elapsed_seconds=self._elapsed or None,
            tool_calls=self.tool_calls or None,
        )

    def can_apply(self, action: str) -> bool:
        if action == RuntimeAction.Replan:
            return self._capabilities.replan
        if action == RuntimeAction.EscalateModel:
            return self._capabilities.model_switch and self._index < len(self._order) - 1
        if action == RuntimeAction.DeescalateModel:
            return self._capabilities.model_switch and self._index > 0
        if action == RuntimeAction.CompactContext:
            return self._capabilities.context_compaction and bool(self.transcript)
        return self._capabilities.supports(action)

    def apply(self, action: str) -> ApplyResult:
        if action == RuntimeAction.Replan:
            self.replans += 1
            self.pending_replan = self._replan_message
            self.record_segment("plan", self._replan_message, replan=True)
            return ApplyResult(applied=True, action=action, reason="injected replan message")
        if action == RuntimeAction.EscalateModel:
            if self._index >= len(self._order) - 1:
                return ApplyResult(applied=False, action=action, reason="already at the top tier")
            self._index += 1
            return ApplyResult(
                applied=True, action=action, reason=f"switched to {self.tier}"
            )
        if action == RuntimeAction.DeescalateModel:
            if self._index <= 0:
                return ApplyResult(
                    applied=False, action=action, reason="already at the bottom tier"
                )
            self._index -= 1
            return ApplyResult(
                applied=True, action=action, reason=f"switched to {self.tier}"
            )
        if action == RuntimeAction.CompactContext:
            if self._compactor is None:
                return ApplyResult(applied=False, action=action, reason="no compactor")
            report = self._compactor.compact(self.transcript)
            self.transcript = list(report.kept)
            self.compactions += 1
            self.last_compaction = report
            return ApplyResult(
                applied=True,
                action=action,
                reason=f"compacted {report.dropped_count} segments",
            )
        return ApplyResult(applied=False, action=action, reason="unsupported action")

    def take_replan(self) -> str | None:
        """Return and clear the pending replan message, if any."""
        message = self.pending_replan
        self.pending_replan = None
        return message
