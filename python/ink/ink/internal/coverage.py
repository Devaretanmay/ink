"""Semantic Coverage Engine: Exact, Semantic, and Unknown decision boundaries."""

from __future__ import annotations

import math
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from .contracts import canonical


class TextVectorizer:
    """Fast, local n-gram sparse vectorizer for state representation (<0.02ms)."""

    def __init__(self, vocab: dict[str, int] | None = None, idf: dict[str, float] | None = None):
        self.vocab = vocab or {}
        self.idf = idf or {}

    @classmethod
    def fit(cls, texts: list[str], max_features: int = 1000) -> TextVectorizer:
        df: Counter[str] = Counter()
        n = len(texts)
        for t in texts:
            words = set(cls._tokenize(t))
            for w in words:
                df[w] += 1
        top = [w for w, _ in df.most_common(max_features)]
        vocab = {w: i for i, w in enumerate(top)}
        idf = {w: math.log((n + 1) / (df[w] + 1)) + 1.0 for w in top}
        return cls(vocab=vocab, idf=idf)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        cleaned = text.lower()
        for ch in ("?", "!", ".", ","):
            cleaned = cleaned.replace(ch, " ")
        words = cleaned.split()
        bigrams = [f"{words[i]}_{words[i+1]}" for i in range(len(words) - 1)]
        return words + bigrams

    def transform(self, text: str) -> np.ndarray:
        if not self.vocab:
            return np.zeros(1, dtype=np.float32)
        vec = np.zeros(len(self.vocab), dtype=np.float32)
        words = self._tokenize(text)
        counts = Counter(words)
        for w, c in counts.items():
            if w in self.vocab:
                vec[self.vocab[w]] = c * self.idf[w]
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec

    def to_dict(self) -> dict[str, Any]:
        return {"vocab": self.vocab, "idf": self.idf}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TextVectorizer:
        return cls(vocab=data.get("vocab", {}), idf=data.get("idf", {}))


def _extract_text(state: dict[str, Any]) -> str:
    for key in ("request", "query", "text", "message", "prompt", "description", "content", "summary", "input"):
        if key in state and isinstance(state[key], str):
            return state[key]
    for val in state.values():
        if isinstance(val, str) and (" " in val or len(val) > 20):
            return val
    return canonical(state)


@dataclass(frozen=True)
class RegionHealth:
    region_id: str
    status: str
    sample_count: int
    verified_quality: float
    quality_lower_bound: float
    comparison_disagreement_rate: float
    distance_mean: float
    distance_p95: float
    negative_margin: float
    radius: float
    counterexample_count: int
    outcome_completeness: float
    last_drift_check: float | None
    last_changed: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SemanticRegion:
    region_id: str
    site: str
    choice: str
    prototype_state: dict[str, Any]
    prototype_vector: list[float]
    radius: float
    negative_margin: float
    member_count: int
    confidence: float
    status: str = "CANDIDATE"  # CANDIDATE -> SHADOW -> ACTIVE
    last_changed: float = field(default_factory=time.time)
    counterexample_count: int = 0
    engine: str = "exact"
    structured_constraints: dict[str, Any] = field(default_factory=dict)
    sample_count: int = 0
    outcome_count: int = 0
    positive_outcomes: int = 0
    quality_lower_bound: float = 0.0
    version: str = "reg_v2"

    def __post_init__(self):
        if not self.sample_count and self.member_count:
            self.sample_count = self.member_count
        elif not self.member_count and self.sample_count:
            self.member_count = self.sample_count

    def distance(self, vector: np.ndarray) -> float:
        proto = np.array(self.prototype_vector, dtype=np.float32)
        cos_sim = float(np.dot(vector, proto))
        return max(0.0, 1.0 - cos_sim)

    def contains(
        self, vector: np.ndarray, state: dict[str, Any] | None = None
    ) -> tuple[bool, float]:
        if state is not None and self.structured_constraints:
            for k, expected in self.structured_constraints.items():
                if state.get(k) != expected:
                    return False, 1.0
        dist = self.distance(vector)
        is_inside = dist <= self.radius and dist < (self.negative_margin * 0.75)
        return is_inside, dist

    def classify_membership(
        self, vector: np.ndarray, state: dict[str, Any] | None = None
    ) -> tuple[str, float]:
        if state is not None and self.structured_constraints:
            for k, expected in self.structured_constraints.items():
                if state.get(k) != expected:
                    return "constraint_mismatch", 1.0
        dist = self.distance(vector)
        if dist <= self.radius and dist < (self.negative_margin * 0.75):
            return "inside", dist
        elif dist <= self.radius * 1.25:
            return "near_boundary", dist
        return "outside", dist

    def tighten_margin(
        self, counterexample_vector: np.ndarray, safety_factor: float = 0.60
    ) -> bool:
        dist = self.distance(counterexample_vector)
        if dist < self.negative_margin:
            self.negative_margin = round(dist, 4)
            self.radius = round(min(self.radius, max(0.0, dist * safety_factor)), 4)
            self.counterexample_count += 1
            self.last_changed = time.time()
            if self.radius < 0.05:
                self.status = "SHADOW"
            return True
        return False
    def detect_multimodal_split(
        self,
        vectors: list[np.ndarray],
        states: list[dict[str, Any]],
        min_region_samples: int = 5,
    ) -> tuple[SemanticRegion, SemanticRegion] | None:
        if len(vectors) < 2 * min_region_samples:
            return None
        c1 = np.array(self.prototype_vector, dtype=np.float32)
        dists = [self.distance(v) for v in vectors]
        max_idx = int(np.argmax(dists))
        c2 = vectors[max_idx]
        if dists[max_idx] < 0.25:
            return None
        p1, p2 = [], []
        for i, v in enumerate(vectors):
            d1 = 1.0 - float(np.dot(v, c1))
            d2 = 1.0 - float(np.dot(v, c2))
            if d1 <= d2:
                p1.append(i)
            else:
                p2.append(i)
        if len(p1) < min_region_samples or len(p2) < min_region_samples:
            return None
        m1 = np.mean([vectors[i] for i in p1], axis=0)
        m2 = np.mean([vectors[i] for i in p2], axis=0)
        n1, n2 = float(np.linalg.norm(m1)), float(np.linalg.norm(m2))
        if n1 > 0:
            m1 /= n1
        if n2 > 0:
            m2 /= n2
        sep = max(0.0, 1.0 - float(np.dot(m1, m2)))
        r1 = float(np.percentile([1.0 - float(np.dot(vectors[i], m1)) for i in p1], 95))
        r2 = float(np.percentile([1.0 - float(np.dot(vectors[i], m2)) for i in p2], 95))
        if sep < 1.3 * max(r1, r2) or max(r1, r2) > 0.80 * self.radius:
            return None
        now = time.time()
        child_a = SemanticRegion(
            region_id=f"{self.region_id}.a",
            site=self.site,
            choice=self.choice,
            prototype_state=states[p1[0]],
            prototype_vector=m1.tolist(),
            radius=round(r1, 4),
            negative_margin=self.negative_margin,
            member_count=len(p1),
            confidence=self.confidence,
            status="SHADOW",
            last_changed=now,
        )
        child_b = SemanticRegion(
            region_id=f"{self.region_id}.b",
            site=self.site,
            choice=self.choice,
            prototype_state=states[p2[0]],
            prototype_vector=m2.tolist(),
            radius=round(r2, 4),
            negative_margin=self.negative_margin,
            member_count=len(p2),
            confidence=self.confidence,
            status="SHADOW",
            last_changed=now,
        )
        return child_a, child_b

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticRegion:
        return cls(**data)


CoverageRegion = SemanticRegion


class CoverageEngine:
    """Manages exact and semantic decision regions with strict abstention."""

    def __init__(
        self,
        exact_coverage: dict[str, dict[str, Any]] | None = None,
        semantic_regions: list[SemanticRegion] | None = None,
        vectorizer: TextVectorizer | None = None,
        ambiguity_margin: float = 0.08,
    ):
        self.exact_coverage = exact_coverage or {}
        self.semantic_regions = semantic_regions or []
        self.vectorizer = vectorizer or TextVectorizer()
        self.ambiguity_margin = float(ambiguity_margin)
        self._sync_matrix()

    def _sync_matrix(self):
        if self.semantic_regions and len(self.semantic_regions[0].prototype_vector) > 0:
            self._matrix = np.array(
                [r.prototype_vector for r in self.semantic_regions], dtype=np.float32
            )
            self._radii = np.array(
                [r.radius for r in self.semantic_regions], dtype=np.float32
            )
        else:
            self._matrix = np.empty((0, 0), dtype=np.float32)
            self._radii = np.empty((0,), dtype=np.float32)

    def route(self, state: dict[str, Any]) -> tuple[str, dict[str, Any] | None, float]:
        """Routes state: returns (level, region_info, confidence/distance).
        
        Levels:
          - "exact": exact state match in qualified coverage
          - "semantic": independently qualified semantic region match
          - "shadow": candidate/shadow semantic region match (record, do not serve)
          - "ambiguous": multiple competing regions disagree or boundary margin is weak
          - "outside_coverage": unknown state falling outside all regions
          - "revoked": region authority was revoked or degraded
        """
        key = canonical(state)
        if key in self.exact_coverage:
            reg = dict(self.exact_coverage[key])
            reg["distance"] = 0.0
            return "exact", reg, reg.get("confidence", 1.0)

        if not self.semantic_regions or not self.vectorizer.vocab:
            return "outside_coverage", None, 0.0

        vec = self.vectorizer.transform(_extract_text(state))
        if self._matrix.size == 0 or len(self.semantic_regions) != len(self._matrix):
            self._sync_matrix()

        if self._matrix.size > 0:
            cos_sims = self._matrix @ vec
            dists = np.clip(1.0 - cos_sims, 0.0, 2.0)
            radii = self._radii
        else:
            dists = np.array([r.distance(vec) for r in self.semantic_regions], dtype=np.float32)
            radii = np.array([r.radius for r in self.semantic_regions], dtype=np.float32)

        matching_indices = np.where(dists <= radii)[0]
        if len(matching_indices) == 0:
            return "outside_coverage", None, 0.0

        matches: list[tuple[SemanticRegion, float]] = []
        for i in matching_indices:
            reg = self.semantic_regions[i]
            inside, dist = reg.contains(vec, state)
            if inside:
                matches.append((reg, float(dist)))
        if not matches:
            return "outside_coverage", None, 0.0

        matches.sort(key=lambda x: x[1])
        top_region, top_dist = matches[0]

        # 1. Multi-match ambiguity check across conflicting choices
        if len(matches) > 1:
            for other_reg, _ in matches[1:]:
                if other_reg.choice != top_region.choice:
                    return "ambiguous", None, top_dist

        # 2. Competing hypothesis boundary margin check across ALL semantic regions
        competing_dists = [
            float(dists[i])
            for i, r in enumerate(self.semantic_regions)
            if r.choice != top_region.choice
        ]
        if competing_dists:
            nearest_competing = min(competing_dists)
            margin = nearest_competing - top_dist
            if margin < self.ambiguity_margin:
                return "ambiguous", None, top_dist

        top_dict = top_region.to_dict()
        top_dict["distance"] = round(top_dist, 4)
        if top_region.status in ("REVOKED", "DEGRADED"):
            return "revoked", top_dict, 0.0
        if top_region.status != "ACTIVE":
            return "shadow", top_dict, top_region.confidence

        return "semantic", top_dict, top_region.confidence

    def ingest_counterexample(self, state: dict[str, Any], counter_choice: str) -> list[str]:
        vec = self.vectorizer.transform(_extract_text(state))
        tightened = []
        for reg in self.semantic_regions:
            if reg.choice != counter_choice and reg.tighten_margin(vec):
                tightened.append(reg.region_id)
        if tightened:
            self._sync_matrix()
        return tightened

    def get_region(self, region_id: str) -> SemanticRegion | None:
        for reg in self.semantic_regions:
            if reg.region_id == region_id:
                return reg
        return None

    def replace_region(
        self, old_region_id: str, new_regions: list[SemanticRegion]
    ) -> bool:
        idx = None
        for i, reg in enumerate(self.semantic_regions):
            if reg.region_id == old_region_id:
                idx = i
                break
        if idx is None:
            return False
        self.semantic_regions.pop(idx)
        for r in new_regions:
            self.semantic_regions.append(r)
        self._sync_matrix()
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "exact_coverage": self.exact_coverage,
            "semantic_regions": [r.to_dict() for r in self.semantic_regions],
            "vectorizer": self.vectorizer.to_dict(),
            "ambiguity_margin": self.ambiguity_margin,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoverageEngine:
        exact = data.get("exact_coverage", {})
        sem = [SemanticRegion.from_dict(r) for r in data.get("semantic_regions", [])]
        vec = TextVectorizer.from_dict(data.get("vectorizer", {}))
        ambiguity_margin = float(data.get("ambiguity_margin", 0.08))
        return cls(
            exact_coverage=exact,
            semantic_regions=sem,
            vectorizer=vec,
            ambiguity_margin=ambiguity_margin,
        )

    def self_tune_from_history(
        self,
        history: list[dict[str, Any]],
        requirements: dict[str, Any],
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> bool:
        """Adapts coverage regions dynamically based on recent production counterexamples and splits."""
        recent_counterexamples = [
            r
            for r in history[-50:]
            if r.get("outcome") and r["outcome"].get("quality", 1.0) < 0.5
        ]
        tightened_any = False
        for ce in recent_counterexamples:
            tightened = self.ingest_counterexample(ce["state"], ce["choice"])
            if tightened:
                tightened_any = True
                if on_event:
                    on_event("region_tightened", {"regions": tightened})

        if requirements.get("allow_region_split", True) and not requirements.get("high_risk", False):
            min_reg_s = requirements.get("min_region_samples", 5)
            split_candidates = []
            for reg in list(self.semantic_regions):
                if reg.status == "ACTIVE" and "." not in reg.region_id:
                    matching = [
                        (self.vectorizer.transform(_extract_text(r["state"])), r["state"])
                        for r in history
                        if reg.contains(self.vectorizer.transform(_extract_text(r["state"])))[0]
                    ]
                    if len(matching) >= 2 * min_reg_s:
                        vecs = [m[0] for m in matching]
                        sts = [m[1] for m in matching]
                        split = reg.detect_multimodal_split(vecs, sts, min_reg_s)
                        if split:
                            split_candidates.extend(split)
                            if on_event:
                                on_event(
                                    "region_split_candidate_created",
                                    {
                                        "parent_region": reg.region_id,
                                        "child_a": split[0].region_id,
                                        "child_b": split[1].region_id,
                                    },
                                )
            for sc in split_candidates:
                self.semantic_regions.append(sc)

        return tightened_any


def calibrate_semantic_boundaries(
    records: list[dict[str, Any]],
    vectorizer: TextVectorizer,
    site_version: str,
    min_region_samples: int = 2,
    max_radius: float = 0.75,
) -> list[SemanticRegion]:
    """Derive calibrated semantic regions and negative margins from observation evidence."""
    by_choice: dict[str, list[tuple[dict[str, Any], np.ndarray]]] = {}
    for r in records:
        vec = vectorizer.transform(_extract_text(r["state"]))
        by_choice.setdefault(r["choice"], []).append((r, vec))

    all_other_vecs = {
        choice: [
            vec
            for other_choice, other_group in by_choice.items()
            if other_choice != choice
            for _, vec in other_group
        ]
        for choice in by_choice
    }

    semantic_regions: list[SemanticRegion] = []
    region_idx = 1

    for choice, group in by_choice.items():
        if len(group) < min_region_samples:
            continue

        other_vecs = all_other_vecs.get(choice, [])
        choice_vecs = [vec for _, vec in group]
        centroid = np.mean(choice_vecs, axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 1e-9:
            centroid /= norm

        # Select medoid (most representative exemplar record)
        med_idx = int(np.argmax([float(np.dot(centroid, v)) for v in choice_vecs]))
        seed_state = group[med_idx][0]["state"]

        neg_dist = 1.0
        if other_vecs:
            neg_sims = [float(np.dot(centroid, ov)) for ov in other_vecs]
            if len(other_vecs) >= 20:
                neg_dist = float(max(0.05, 1.0 - np.percentile(neg_sims, 98)))
            else:
                neg_dist = float(max(0.05, 1.0 - max(neg_sims)))

        radius = min(max_radius, max(0.15, neg_dist * 0.95))
        neg_margin = max(neg_dist, radius / 0.74)

        region = SemanticRegion(
            region_id=f"sem-{site_version[:8]}-{region_idx:03d}",
            site=site_version,
            choice=choice,
            prototype_state=seed_state,
            prototype_vector=centroid.tolist(),
            radius=round(radius, 4),
            negative_margin=round(neg_margin, 4),
            member_count=len(group),
            confidence=0.85,
            status="SHADOW",
        )
        semantic_regions.append(region)
        region_idx += 1

    return semantic_regions
