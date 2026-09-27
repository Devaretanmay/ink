"""Runtime state: the resources and execution conditions behind progress.

Mirrors the Rust ``runtime::state`` types. Every field is optional so an
integration can adopt Microloop incrementally. Microloop never prices a model:
``cost`` is whatever the host measured.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["Budget", "RuntimeState", "Usage"]


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


@dataclass
class Usage:
    """Cumulative resource usage for a run."""

    cost: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    steps: int | None = None
    elapsed_seconds: float | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> Usage:
        if not payload:
            return cls()
        return cls(
            cost=_float(payload.get("cost")),
            input_tokens=_int(payload.get("input_tokens")),
            output_tokens=_int(payload.get("output_tokens")),
            steps=_int(payload.get("steps")),
            elapsed_seconds=_float(payload.get("elapsed_seconds")),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "cost": self.cost,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "steps": self.steps,
            "elapsed_seconds": self.elapsed_seconds,
        }
        return {key: value for key, value in payload.items() if value is not None}

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)


@dataclass
class Budget:
    """Execution budgets. An unset bound is unconstrained."""

    max_cost: float | None = None
    max_tokens: int | None = None
    max_steps: int | None = None
    max_seconds: float | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> Budget:
        if not payload:
            return cls()
        return cls(
            max_cost=_float(payload.get("max_cost")),
            max_tokens=_int(payload.get("max_tokens")),
            max_steps=_int(payload.get("max_steps")),
            max_seconds=_float(payload.get("max_seconds")),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "max_cost": self.max_cost,
            "max_tokens": self.max_tokens,
            "max_steps": self.max_steps,
            "max_seconds": self.max_seconds,
        }
        return {key: value for key, value in payload.items() if value is not None}


@dataclass
class RuntimeState:
    """A snapshot of the execution conditions reported with one step."""

    model: str | None = None
    context_tokens: int | None = None
    context_limit: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None
    elapsed_seconds: float | None = None
    tool_calls: int | None = None
    remaining_budget: float | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> RuntimeState:
        if not payload:
            return cls()
        cost = payload.get("cost", payload.get("estimated_cost"))
        return cls(
            model=payload.get("model"),
            context_tokens=_int(payload.get("context_tokens")),
            context_limit=_int(payload.get("context_limit")),
            input_tokens=_int(payload.get("input_tokens")),
            output_tokens=_int(payload.get("output_tokens")),
            cost=_float(cost),
            elapsed_seconds=_float(payload.get("elapsed_seconds")),
            tool_calls=_int(payload.get("tool_calls")),
            remaining_budget=_float(payload.get("remaining_budget")),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "context_tokens": self.context_tokens,
            "context_limit": self.context_limit,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost": self.cost,
            "elapsed_seconds": self.elapsed_seconds,
            "tool_calls": self.tool_calls,
            "remaining_budget": self.remaining_budget,
        }
        return {key: value for key, value in payload.items() if value is not None}

    @property
    def estimated_cost(self) -> float | None:
        """Back-compatible alias for :attr:`cost`."""
        return self.cost

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    @property
    def context_utilization(self) -> float | None:
        if self.context_tokens is None or not self.context_limit:
            return None
        return self.context_tokens / self.context_limit

    def usage(self) -> Usage:
        return Usage(
            cost=self.cost,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            elapsed_seconds=self.elapsed_seconds,
        )
