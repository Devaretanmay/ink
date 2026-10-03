"""Versioned, JSON-only decision contracts."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class _Schema(dict):
    """Immutable mapping that still round-trips through dataclasses.asdict/JSON."""

    def _immutable(self, *args, **kwargs):
        raise TypeError("DecisionSite schema is immutable; register a new contract")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable
    __ior__ = _immutable


@dataclass(frozen=True)
class DecisionSite:
    name: str
    state_schema: dict[str, str]
    choices: tuple[str, ...]
    fallback_revision: str = "1"
    fallback_model_calls: int | None = None

    def __post_init__(self):
        if isinstance(self.choices, str):
            raise ValueError("Choices must be a sequence of labels, not a string")
        object.__setattr__(self, "choices", tuple(self.choices))
        object.__setattr__(self, "state_schema", _Schema(self.state_schema))
        if self.fallback_model_calls is not None and (
            type(self.fallback_model_calls) is not int or self.fallback_model_calls < 0
        ):
            raise ValueError("fallback_model_calls must be a nonnegative fixed call count")
        if (
            not isinstance(self.name, str)
            or not self.name
            or not isinstance(self.fallback_revision, str)
            or not self.fallback_revision
        ):
            raise ValueError("Site name and fallback revision must be nonempty")
        if (
            len(self.choices) < 2
            or any(not isinstance(c, str) or not c for c in self.choices)
            or len(set(self.choices)) != len(self.choices)
        ):
            raise ValueError("Choices must contain at least two unique nonempty strings")
        for name, kind in self.state_schema.items():
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(kind, str)
                or kind
                not in {
                    "string",
                    "integer",
                    "number",
                    "boolean",
                    "string?",
                    "integer?",
                    "number?",
                    "boolean?",
                }
            ):
                raise ValueError("Schema fields use string, integer, number, boolean, optionally ?")

    @property
    def version(self) -> str:
        contract = asdict(self)
        if self.fallback_model_calls is None:
            contract.pop("fallback_model_calls")  # Preserve existing v0.4 contract hashes.
        return digest(contract)

    def encode(self, state: dict) -> dict:
        if not isinstance(state, dict):
            raise TypeError(f"State must be a dict, got {type(state).__name__}")
        extra = sorted(set(state) - set(self.state_schema))
        if extra:
            raise ValueError(
                f"State must be an object with only declared fields; found undeclared {extra}. "
                f"Declared schema fields: {sorted(self.state_schema)}"
            )
        encoded = {}
        for name, kind in self.state_schema.items():
            value = state.get(name)
            if value is None and kind.endswith("?"):
                encoded[name] = None
                continue
            kind = kind.rstrip("?")
            valid = {
                "string": type(value) is str,
                "integer": type(value) is int,
                "boolean": type(value) is bool,
                "number": type(value) in (int, float),
            }[kind]
            if not valid or (kind == "number" and not math.isfinite(value)):
                if value is None:
                    raise ValueError(
                        f"Invalid state field {name!r}: missing required field (expected {kind})"
                    )
                raise ValueError(
                    f"Invalid state field {name!r}: expected {kind}, got {type(value).__name__}"
                )
            encoded[name] = float(value) if kind == "number" else value
        canonical(encoded)
        return encoded


@dataclass(frozen=True)
class FallbackResult:
    choice: str
    model_calls: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None
    provider: str | None = None
    model: str | None = None
    request_attempts: int | None = None

    def __post_init__(self):
        for key in ("model_calls", "input_tokens", "output_tokens", "request_attempts"):
            value = getattr(self, key)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{key} must be a nonnegative integer")
        if self.cost is not None and (not math.isfinite(self.cost) or self.cost < 0):
            raise ValueError("cost must be finite and nonnegative")


@dataclass(frozen=True)
class DecisionResult:
    choice: str
    decision_id: str | None
    source: str
    site_version: str
    fast_path_version: str | None = None
    fallback_reason: str | None = None
    confidence: float | None = None
    recorded: bool = True
    receipt: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Outcome:
    quality: float
    verifier: str
    verifier_version: str
    evidence: dict = field(default_factory=dict)

    def __post_init__(self):
        if not math.isfinite(self.quality) or not 0 <= self.quality <= 1:
            raise ValueError("Quality must be finite and between zero and one")
        if not self.verifier or not self.verifier_version or not isinstance(self.evidence, dict):
            raise ValueError("Outcome requires verifier identity, version, and evidence dict")
        canonical(self.evidence)


@dataclass(frozen=True)
class PromotionRequirements:
    """Explicit experiment settings, not universal production defaults."""

    min_samples: int
    min_quality: float
    min_confidence: float
    max_degradation: float
    comparison_rate: float
    min_region_samples: int
    evaluation_window: int
    max_uncovered_rate: float = 1.0
    min_comparison_rate: float = 0.05
    allow_adaptive_comparison: bool = True
    allow_region_split: bool = True
    allow_auto_requalify: bool = True
    high_risk: bool = False

    def __post_init__(self):
        for key in ("min_samples", "min_region_samples", "evaluation_window"):
            if type(getattr(self, key)) is not int or getattr(self, key) < 1:
                raise ValueError(f"{key} must be a positive integer")
        for key in (
            "min_quality",
            "min_confidence",
            "max_degradation",
            "comparison_rate",
            "min_comparison_rate",
            "max_uncovered_rate",
        ):
            if not math.isfinite(getattr(self, key)) or not 0 <= getattr(self, key) <= 1:
                raise ValueError(f"{key} must be between zero and one")
        if not 0 < self.comparison_rate < 1:
            raise ValueError("Active service requires a nonzero fallback comparison sample")
        if not 0 < self.min_comparison_rate <= self.comparison_rate:
            raise ValueError("min_comparison_rate must be nonzero and <= comparison_rate")
        if self.evaluation_window < 2 * self.min_samples:
            raise ValueError("evaluation_window must accommodate both comparison arms")
        if self.high_risk:
            object.__setattr__(self, "allow_adaptive_comparison", False)
            object.__setattr__(self, "allow_region_split", False)
            object.__setattr__(self, "allow_auto_requalify", False)
