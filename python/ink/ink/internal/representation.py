"""Generalized State Representation Layer.

Transforms raw DecisionSite state into deterministic, structured, and
semantic representations suitable for exact identity, semantic similarity,
coverage boundaries, and local engine execution.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import numpy as np

from .contracts import canonical

REPRESENTATION_VERSION = "rep_v2"


class RepresentationStrategy(StrEnum):
    """Compiler feature families; Small remains optional and control-plane only."""

    LEXICAL = "lexical"
    SMALL = "small"
    STRUCTURED_LEXICAL = "structured_lexical"
    STRUCTURED_SMALL = "structured_small"

    @property
    def uses_small(self) -> bool:
        return self in (self.SMALL, self.STRUCTURED_SMALL)

    @property
    def uses_structured(self) -> bool:
        return self in (self.STRUCTURED_LEXICAL, self.STRUCTURED_SMALL)


@dataclass(frozen=True)
class RepresentedState:
    """Internal canonical representation of DecisionSite input state."""

    exact_key: str
    exact_features: dict[str, Any]
    structured_features: dict[str, float]
    semantic_features: dict[str, str]
    semantic_text: str
    semantic_vector: list[float]
    representation_version: str = REPRESENTATION_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepresentedState:
        return cls(
            exact_key=data["exact_key"],
            exact_features=data.get("exact_features", {}),
            structured_features=data.get("structured_features", {}),
            semantic_features=data.get("semantic_features", {}),
            semantic_text=data.get("semantic_text", ""),
            semantic_vector=data.get("semantic_vector", []),
            representation_version=data.get("representation_version", REPRESENTATION_VERSION),
        )


@runtime_checkable
class SemanticEncoder(Protocol):
    """Protocol for local, deterministic semantic vector encoders."""

    name: str

    def transform(self, text: str) -> np.ndarray: ...
    def to_dict(self) -> dict[str, Any]: ...


class SparseTfidfEncoder:
    """Deterministic, local n-gram sparse vectorizer (<0.02ms inference)."""

    name = "sparse_tfidf_v2"

    def __init__(
        self,
        vocab: dict[str, int] | None = None,
        idf: dict[str, float] | None = None,
        max_features: int = 1000,
    ):
        self.vocab = vocab or {}
        self.idf = idf or {}
        self.max_features = max_features

    @classmethod
    def fit(cls, texts: list[str], max_features: int = 1000) -> SparseTfidfEncoder:
        df: dict[str, int] = {}
        n = len(texts)
        for t in texts:
            words = set(cls._tokenize(t))
            for w in words:
                df[w] = df.get(w, 0) + 1

        # Deterministic sorting: highest df first, alphabetical tiebreaker
        sorted_tokens = sorted(df.keys(), key=lambda w: (-df[w], w))[:max_features]
        vocab = {w: i for i, w in enumerate(sorted_tokens)}
        idf = {w: float(math.log((n + 1.0) / (df[w] + 1.0)) + 1.0) for w in sorted_tokens}
        return cls(vocab=vocab, idf=idf, max_features=max_features)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        cleaned = text.lower()
        for ch in ("?", "!", ".", ",", ":", ";", "(", ")", "[", "]", "{", "}", "\"", "'", "/", "\\", "\n", "\t"):
            cleaned = cleaned.replace(ch, " ")
        words = [w for w in cleaned.split() if len(w) > 1]
        bigrams = [f"{words[i]}_{words[i+1]}" for i in range(len(words) - 1)]
        return words + bigrams

    def transform(self, text: str) -> np.ndarray:
        if not self.vocab:
            return np.zeros(1, dtype=np.float32)
        vec = np.zeros(len(self.vocab), dtype=np.float32)
        words = self._tokenize(text)
        counts: dict[str, int] = {}
        for w in words:
            counts[w] = counts.get(w, 0) + 1
        for w, c in counts.items():
            if w in self.vocab:
                # Sublinear term frequency scaling: 1 + log(c)
                tf = 1.0 + math.log(c)
                vec[self.vocab[w]] = tf * self.idf[w]
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "vocab": self.vocab,
            "idf": self.idf,
            "max_features": self.max_features,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SparseTfidfEncoder:
        return cls(
            vocab=data.get("vocab", {}),
            idf=data.get("idf", {}),
            max_features=data.get("max_features", 1000),
        )


def _is_semantic_text(value: Any) -> bool:
    """Detect if a string field contains natural language or freeform text."""
    if not isinstance(value, str):
        return False
    # Strings with spaces, punctuation, or significant length represent freeform text
    if " " in value.strip() or len(value) > 24 or any(p in value for p in (".", "?", "!", "/", "_")):
        return True
    return False


class StateRepresentationLayer:
    """Transforms raw DecisionSite states into structured and semantic representations."""

    def __init__(
        self,
        schema: dict[str, str],
        encoder: SemanticEncoder | None = None,
        version: str = REPRESENTATION_VERSION,
    ):
        self.schema = schema
        self.encoder = encoder or SparseTfidfEncoder()
        self.version = version

    def fit_from_records(self, records: list[dict[str, Any]]) -> StateRepresentationLayer:
        """Fit semantic encoders on raw observed states."""
        texts = [self.extract_semantic_text(r.get("state", r)) for r in records]
        non_empty = [t for t in texts if t.strip()]
        if non_empty:
            fitted_encoder = SparseTfidfEncoder.fit(non_empty)
            return StateRepresentationLayer(self.schema, fitted_encoder, self.version)
        return self

    def extract_semantic_text(self, state: dict[str, Any]) -> str:
        """Extract or compose normalized text from semantic fields."""
        text_parts = []
        for name in sorted(self.schema.keys()):
            val = state.get(name)
            if val is not None and isinstance(val, str) and _is_semantic_text(val):
                text_parts.append(val.strip())

        if text_parts:
            return " ".join(text_parts)

        # Fallback to checking standard text field names
        for key in ("request", "query", "text", "message", "prompt", "description", "content"):
            if key in state and isinstance(state[key], str):
                return state[key].strip()

        # If pure structured state, use canonical JSON representation
        return canonical(state)

    def represent(self, state: dict[str, Any]) -> RepresentedState:
        """Represent raw state into structured, exact, and semantic partitions."""
        exact_features: dict[str, Any] = {}
        structured_features: dict[str, float] = {}
        semantic_features: dict[str, str] = {}

        for name in sorted(self.schema.keys()):
            val = state.get(name)
            kind = self.schema[name].rstrip("?")
            if val is None:
                continue

            if kind in ("integer", "number"):
                structured_features[name] = float(val)
                # Small integers often act as categorical codes (e.g., status 0, 1, 2)
                if kind == "integer" and -100 <= val <= 100:
                    exact_features[name] = val
            elif kind == "boolean":
                exact_features[name] = bool(val)
                structured_features[name] = 1.0 if val else 0.0
            elif kind == "string":
                if _is_semantic_text(val):
                    semantic_features[name] = val
                else:
                    exact_features[name] = val

        semantic_text = self.extract_semantic_text(state)
        sem_vec = self.encoder.transform(semantic_text) if self.encoder else np.zeros(1, dtype=np.float32)

        return RepresentedState(
            exact_key=canonical(exact_features if exact_features else state),
            exact_features=exact_features,
            structured_features=structured_features,
            semantic_features=semantic_features,
            semantic_text=semantic_text,
            semantic_vector=sem_vec.tolist(),
            representation_version=self.version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "version": self.version,
            "encoder": self.encoder.to_dict() if hasattr(self.encoder, "to_dict") else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateRepresentationLayer:
        enc_data = data.get("encoder")
        encoder = SparseTfidfEncoder.from_dict(enc_data) if enc_data else SparseTfidfEncoder()
        return cls(
            schema=data.get("schema", {}),
            encoder=encoder,
            version=data.get("version", REPRESENTATION_VERSION),
        )
