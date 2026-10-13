"""Versioned, JSON-only decision contracts."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from ..errors import ContractError


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


class PolicyRepresentationState(StrEnum):
    """Lifecycle state governing whether Small representations are used for compilation."""

    UNKNOWN = "UNKNOWN"
    EVALUATING = "EVALUATING"
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class PolicyUtilityEvidence:
    """Historical record of Small representation utility decision for a site."""

    site: str
    site_version: str
    checkpoint_revision: str
    classical_score: float
    small_score: float
    classical_qualified_coverage: float
    small_qualified_coverage: float
    classical_verified_quality: float
    small_verified_quality: float
    host_calls_delta: int
    compute_cost_seconds: float
    decision: str
    reason: str


@dataclass(frozen=True)
class DecisionSite:
    name: str
    state_schema: dict[str, str]
    choices: tuple[str, ...]
    fallback_revision: str = "1"
    fallback_model_calls: int | None = None
    description: str = ""
    instructions: str | None = None
    local_error_budget: float | None = None

    def __post_init__(self):
        if isinstance(self.choices, str):
            raise ContractError("Choices must be a sequence of labels, not a string")
        object.__setattr__(self, "choices", tuple(self.choices))
        object.__setattr__(self, "state_schema", _Schema(self.state_schema))
        if self.fallback_model_calls is not None and (
            type(self.fallback_model_calls) is not int or self.fallback_model_calls < 0
        ):
            raise ContractError("fallback_model_calls must be a nonnegative fixed call count")
        if (
            not isinstance(self.name, str)
            or not self.name
            or not isinstance(self.fallback_revision, str)
            or not self.fallback_revision
        ):
            raise ContractError("Site name and fallback revision must be nonempty")
        if (
            len(self.choices) < 2
            or any(not isinstance(c, str) or not c for c in self.choices)
            or len(set(self.choices)) != len(self.choices)
        ):
            raise ContractError("Choices must contain at least two unique nonempty strings")
        # Canonically normalize float -> number aliases
        normalized_schema = {}
        for name, kind in self.state_schema.items():
            if kind == "float":
                kind = "number"
            elif kind == "float?":
                kind = "number?"
            normalized_schema[name] = kind
        object.__setattr__(self, "state_schema", _Schema(normalized_schema))

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
                raise ContractError("Schema fields use string, integer, number, boolean, optionally ?")

    @property
    def version(self) -> str:
        contract = asdict(self)
        contract.pop("description", None)  # Metadata only; preserve contract hashes.
        if self.instructions is None:
            contract.pop("instructions", None)
        if self.local_error_budget is None:
            contract.pop("local_error_budget", None)
        if self.fallback_model_calls is None:
            contract.pop("fallback_model_calls")  # Preserve existing v0.4 contract hashes.
        return digest(contract)

    def encode(self, state: dict) -> dict:
        if not isinstance(state, dict):
            raise TypeError(f"State must be a dict, got {type(state).__name__}")
        extra = sorted(set(state) - set(self.state_schema))
        if extra:
            raise ContractError(
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
                    raise ContractError(
                        f"Invalid state field {name!r}: missing required field (expected {kind})"
                    )
                raise ContractError(
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

    def __str__(self) -> str:
        return self.choice


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

    min_samples: int = 10
    min_quality: float = 0.8
    min_confidence: float = 0.7
    max_degradation: float = 0.15
    comparison_rate: float = 0.25
    min_region_samples: int = 3
    evaluation_window: int = 50
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


@dataclass
class InkArtifact:
    """Formalized representation of a compiled and qualified behavior artifact."""

    id: str
    site_version: str
    selected_engine: str
    status: str
    epoch: float
    created: float
    representation_version: str
    checksum: str = ""
    site: str = ""
    engine_metadata: dict[str, Any] = field(default_factory=dict)
    selection_record: dict[str, Any] = field(default_factory=dict)
    exact_coverage_count: int = 0
    semantic_region_count: int = 0
    qualified_semantic_region_count: int = 0
    requirements: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any] | InkArtifact) -> InkArtifact:
        if isinstance(row, cls):
            return row
        art_id = row["id"]
        site_version = row["site"]
        status = row["status"]
        epoch = float(row.get("epoch", 0.0))

        payload_raw = row.get("payload")
        if isinstance(payload_raw, str):
            payload = json.loads(payload_raw)
        elif isinstance(payload_raw, dict):
            payload = payload_raw
        else:
            payload = {}

        profile_raw = row.get("profile")
        if isinstance(profile_raw, str):
            profile = json.loads(profile_raw)
        elif isinstance(profile_raw, dict):
            profile = profile_raw
        else:
            profile = None

        evidence_raw = row.get("evidence")
        if isinstance(evidence_raw, str):
            evidence = json.loads(evidence_raw)
        elif isinstance(evidence_raw, dict):
            evidence = evidence_raw
        else:
            evidence = None

        engine_data = payload.get("engine_data", {})
        selected_engine = (
            engine_data.get("engine", "exact") if isinstance(engine_data, dict) else "exact"
        )

        rep_data = payload.get("representation", {})
        rep_version = rep_data.get("version", "none") if isinstance(rep_data, dict) else "none"

        created = float(payload.get("created", epoch))

        exact_count = 0
        semantic_count = 0
        if profile and isinstance(profile, dict):
            cov = profile.get("coverage", {})
            exact_count = len(cov) if isinstance(cov, dict) else 0
            cov_eng = profile.get("coverage_engine", {})
            if isinstance(cov_eng, dict):
                sem_regs = cov_eng.get("semantic_regions", [])
                semantic_count = len(sem_regs) if isinstance(sem_regs, list) else 0

        qualified_count = 0
        if evidence and isinstance(evidence, dict):
            q_regs = evidence.get("qualified_semantic_regions", [])
            qualified_count = len(q_regs) if isinstance(q_regs, list) else 0

        reqs = profile.get("requirements", {}) if profile and isinstance(profile, dict) else {}

        return cls(
            id=art_id,
            site_version=site_version,
            selected_engine=selected_engine,
            status=status,
            epoch=epoch,
            created=created,
            representation_version=rep_version,
            checksum=str(row.get("checksum", "")),
            site=site_version,
            engine_metadata=engine_data if isinstance(engine_data, dict) else {},
            selection_record=payload.get("selection_record") or {},
            exact_coverage_count=exact_count,
            semantic_region_count=semantic_count,
            qualified_semantic_region_count=qualified_count,
            requirements=reqs if isinstance(reqs, dict) else {},
            payload=payload,
            profile=profile,
            evidence=evidence,
        )

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE"

    @property
    def is_shadow(self) -> bool:
        return self.status == "SHADOW"

    @property
    def is_retired(self) -> bool:
        return self.status == "RETIRED"

    @property
    def is_verified(self) -> bool:
        return self.status == "VERIFIED"

    @property
    def is_candidate(self) -> bool:
        return self.status == "CANDIDATE"

    def __getitem__(self, key: str) -> Any:
        if key == "site":
            return self.site_version
        if hasattr(self, key):
            return getattr(self, key)
        if isinstance(self.payload, dict) and key in self.payload:
            return self.payload[key]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: str) -> bool:
        return key == "site" or hasattr(self, key) or (isinstance(self.payload, dict) and key in self.payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MaintenanceOutcome(dict):
    """Dictionary outcome carrying status, reason, and action properties."""

    def __getitem__(self, key):
        if key not in self:
            if key == "status":
                if "deferred" in self:
                    return "deferred"
                if "pending" in self:
                    val = str(self.get("pending", "")).lower()
                    if any(s in val for s in ("sqlite", "error", "unavailable", "locked")):
                        return "failed"
                    return "blocked"
                return "completed"
            if key == "reason":
                return self.get("deferred") or self.get("pending") or self.get("reason")
            if key == "action":
                if "compiled" in self:
                    return "compiled"
                if "demoted" in self:
                    return "demoted"
                return self.get("action")
        return super().__getitem__(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    @property
    def status(self):
        return self["status"]

    @property
    def reason(self):
        return self["reason"]



