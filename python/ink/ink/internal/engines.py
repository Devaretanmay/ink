"""Private local engine boundary and Policy Model backends."""

from __future__ import annotations

import hashlib
import json
import math
import sys
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .contracts import canonical
from .model.constants import (
    INK_DECISION_LARGE,
    INK_DECISION_SMALL,
    LEGACY_DECISION_V1,
    LEGACY_INK_DECISION_V1,
    MODEL_RUNTIME_VERSION,
    resolve_policy_model_id,
)
from .model.policy_registry import get_model_spec


def _require_neural_dependencies():
    if sys.platform == "win32":
        raise OSError(
            f"{INK_DECISION_SMALL} requires Linux or macOS (MLX has no Windows build); "
            "use engine='exact' on Windows"
        )
    try:
        import mlx.core  # noqa: F401
    except ImportError as exc:
        msg = (
            "MLX is not installed; reinstall ink: "
            "pip install --force-reinstall ink-jit"
        )
        raise RuntimeError(msg) from exc


@dataclass
class PolicyProposal:
    """Bounded decision proposal produced by a Policy Model.

    Zero serving authority is conveyed by this proposal alone.
    Ink qualification and evidence decide all serving authority.
    """

    choice: str
    scores: dict[str, float]
    confidence: float
    ambiguity: float | None = None
    familiarity: float | None = None
    act_probability: float | None = None


@runtime_checkable
class PolicyModelBackend(Protocol):
    """Minimal interface for local neural Policy Model backends."""

    model_id: str
    model_version: str
    checkpoint_revision: str
    manifest_sha256: str
    backend_name: str
    checkpoint: str

    def load(self) -> None: ...
    def propose(self, state: dict, choices: list[str], instructions: str | None = None) -> PolicyProposal: ...
    def representation(
        self, state: dict, choices: list[str], instructions: str | None = None
    ) -> list[float] | None: ...
    def close(self) -> None: ...


class InkDecisionSmallBackend:
    """Canonical Primary local Policy Model (421M ModernBERT-large MLX)."""

    def __init__(self, checkpoint: str | None = None) -> None:
        from .model.registry import model_path
        self.model_id = INK_DECISION_SMALL
        spec = get_model_spec(INK_DECISION_SMALL)
        self.model_version = spec.model_version
        self.checkpoint_revision = spec.checkpoint_revision
        self.manifest_sha256 = spec.manifest_sha256
        self.backend_name = "mlx"
        self.checkpoint = (
            str(Path(checkpoint).expanduser().resolve())
            if checkpoint
            else str(model_path(INK_DECISION_SMALL))
        )
        self._managed_checkpoint = checkpoint is None
        self._agent = None
        self._lock = threading.RLock()

    def load(self) -> None:
        if self._agent is not None:
            return
        with self._lock:
            if self._agent is not None:
                return
            _require_neural_dependencies()
            from .model.agent import load
            from .model.registry import ensure_installed, verify
            if self._managed_checkpoint:
                ensure_installed(auto_download=False, model_id=INK_DECISION_SMALL)
                verify(self.checkpoint, model_id=INK_DECISION_SMALL)
            self._agent = load(self.checkpoint)

    def propose(self, state: dict, choices: list[str], instructions: str | None = None) -> PolicyProposal:
        self.load()
        ins = instructions or "Choose the correct decision."
        raw = self._agent.predict(
            state,
            {"decision": {"type": "choice", "criteria": choices, "instructions": ins}},
        )["answers"]["decision"]

        choice = raw["choice"]
        scores = raw.get("probabilities", {})
        conf = float(raw.get("confidence", 0.0))
        act_prob = raw.get("action", {}).get("act_probability")

        # Ambiguity signal: 1.0 - margin between top-2 choices
        sorted_probs = sorted(scores.values(), reverse=True)
        margin = (sorted_probs[0] - sorted_probs[1]) if len(sorted_probs) > 1 else sorted_probs[0]
        ambiguity = round(1.0 - margin, 4)

        return PolicyProposal(
            choice=choice,
            scores=scores,
            confidence=conf,
            ambiguity=ambiguity,
            familiarity=None,
            act_probability=act_prob,
        )

    def representation(
        self, state: dict, choices: list[str], instructions: str | None = None
    ) -> list[float] | None:
        self.load()
        return self._agent.representation(state, choices, instructions)

    def close(self) -> None:
        with self._lock:
            self._agent = None


class InkDecisionLargeBackend:
    """Optional High-Capability Policy Model (486M DeBERTa-v3-large)."""

    def __init__(self, checkpoint: str | None = None) -> None:
        from .model.registry import model_path
        self.model_id = INK_DECISION_LARGE
        spec = get_model_spec(INK_DECISION_LARGE)
        self.model_version = spec.model_version
        self.checkpoint_revision = spec.checkpoint_revision
        self.manifest_sha256 = spec.manifest_sha256
        self.backend_name = "gliner_torch"
        self.checkpoint = (
            str(Path(checkpoint).expanduser().resolve())
            if checkpoint
            else str(model_path(INK_DECISION_LARGE))
        )
        self._managed_checkpoint = checkpoint is None
        self._extractor = None
        self._lock = threading.RLock()

    def load(self) -> None:
        if self._extractor is not None:
            return
        with self._lock:
            if self._extractor is not None:
                return
            try:
                from gliner2 import AutoExtractor
            except ImportError as exc:
                raise RuntimeError(
                    "gliner2 is not installed; install ink[large] to use ink-decision-large: "
                    "pip install 'ink-jit[large]' (or pip install gliner2 torch transformers)"
                ) from exc
            self._extractor = AutoExtractor.from_pretrained(self.checkpoint)

    def propose(self, state: dict, choices: list[str], instructions: str | None = None) -> PolicyProposal:
        self.load()
        text = state.get("text") or state.get("query") or json.dumps(state, sort_keys=True)
        res = self._extractor.classify_text(
            text, {"intent": choices}, include_confidence=True, format_results=True
        )
        intent = res.get("intent", {})
        if isinstance(intent, dict) and "label" in intent:
            choice = intent["label"]
            conf = float(intent.get("confidence", 0.5))
            k = len(choices)
            rem = (1.0 - conf) / max(1, k - 1)
            scores = {c: (conf if c == choice else rem) for c in choices}
        elif isinstance(intent, str):
            choice = intent
            conf = 1.0
            scores = {c: (1.0 if c == choice else 0.0) for c in choices}
        else:
            choice = choices[0]
            conf = 0.0
            scores = {c: 1.0 / len(choices) for c in choices}

        sorted_probs = sorted(scores.values(), reverse=True)
        margin = (sorted_probs[0] - sorted_probs[1]) if len(sorted_probs) > 1 else sorted_probs[0]
        ambiguity = round(1.0 - margin, 4)

        return PolicyProposal(
            choice=choice,
            scores=scores,
            confidence=round(conf, 4),
            ambiguity=ambiguity,
            familiarity=None,  # Large does not fake familiarity
            act_probability=None,
        )

    def representation(
        self, state: dict, choices: list[str], instructions: str | None = None
    ) -> list[float] | None:
        return None

    def close(self) -> None:
        with self._lock:
            self._extractor = None


class AutoPolicyBackend:
    """Conservative Auto Backend: Small-first with graceful fallback."""

    def __init__(self, small_checkpoint: str | None = None, large_checkpoint: str | None = None) -> None:
        self.small = InkDecisionSmallBackend(small_checkpoint)
        self.large = InkDecisionLargeBackend(large_checkpoint)
        self.model_id = "auto"
        self.model_version = self.small.model_version
        self.checkpoint_revision = self.small.checkpoint_revision
        self.manifest_sha256 = self.small.manifest_sha256
        self.backend_name = "auto"
        self.checkpoint = self.small.checkpoint

    def load(self) -> None:
        self.small.load()

    def propose(self, state: dict, choices: list[str], instructions: str | None = None) -> PolicyProposal:
        try:
            return self.small.propose(state, choices, instructions)
        except Exception:
            try:
                return self.large.propose(state, choices, instructions)
            except Exception:
                raise

    def representation(
        self, state: dict, choices: list[str], instructions: str | None = None
    ) -> list[float] | None:
        return self.small.representation(state, choices, instructions)

    def close(self) -> None:
        self.small.close()
        self.large.close()


@runtime_checkable
class DecisionEngine(Protocol):
    name: str

    def compile(self, site, rows) -> dict: ...
    def predict(self, payload: dict, state: dict) -> tuple[str, float]: ...


class ExactEngine:
    """Ultra-fast qualified execution tier for proven repeated states."""

    name = "exact"

    def compile(self, site, rows):
        groups = {}
        for row in rows:
            groups.setdefault(canonical(row["state"]), Counter())[row["choice"]] += 1
        return {
            "engine": self.name,
            "table": {
                key: {
                    "choice": counts.most_common(1)[0][0],
                    "probability": counts.most_common(1)[0][1] / counts.total(),
                }
                for key, counts in groups.items()
            },
        }

    def predict(self, payload, state):
        entry = payload["table"][canonical(state)]
        return entry["choice"], entry["probability"]


class PolicyModelEngine:
    """Architectural engine abstraction connecting Ink to local Policy Models.

    Generates candidate bounded proposals for the Decision Engine. Candidate
    predictions have zero serving authority until independently qualified.
    """

    name = "decision"
    LEGACY_KEYS = (LEGACY_INK_DECISION_V1, LEGACY_DECISION_V1)

    def __init__(
        self,
        checkpoint: str | None = None,
        *,
        backend: PolicyModelBackend | None = None,
        model: str | None = "small",
        instructions: str | None = None,
    ):
        if backend is not None:
            self.backend = backend
        else:
            resolved_id = resolve_policy_model_id(model) or INK_DECISION_SMALL
            if resolved_id == INK_DECISION_LARGE:
                self.backend = InkDecisionLargeBackend(checkpoint)
            elif resolved_id == "auto":
                self.backend = AutoPolicyBackend(checkpoint)
            else:
                self.backend = InkDecisionSmallBackend(checkpoint)

        self.checkpoint = self.backend.checkpoint
        self.instructions = instructions
        self._agents = {}
        self._lock = threading.RLock()

    @staticmethod
    def _manifest(path):
        root = Path(path)
        required = [
            root / name
            for name in ("model.safetensors", "rl_agent_config.json", "encoder/config.json")
        ]
        required += sorted((root / "tokenizer").glob("*"))
        if len(required) < 4 or not all(p.is_file() for p in required):
            # If it's a large model checkpoint, check for config.json and model.safetensors
            if (root / "model.safetensors").is_file() and (root / "config.json").is_file():
                required = [root / "model.safetensors", root / "config.json"]
            else:
                raise FileNotFoundError(
                    "Ink model is missing or incomplete; run ink model-install"
                )
        result = {}
        for file in required:
            h = hashlib.sha256()
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
            result[str(file.relative_to(root))] = h.hexdigest()
        return result

    def compile(self, site, rows):
        _require_neural_dependencies()
        from .model.registry import ensure_installed, verify

        if getattr(self.backend, "_managed_checkpoint", False):
            ensure_installed(auto_download=False, model_id=self.backend.model_id)
            verify(self.checkpoint, model_id=self.backend.model_id)

        examples = []
        seen = set()
        for row in rows:
            key = canonical(row["state"])
            if key not in seen:
                examples.append({"state": row["state"], "choice": row["choice"]})
                seen.add(key)
            if len(examples) == 3:
                break

        site_ins = getattr(site, "instructions", None) or (site.description if site.description else None)
        chosen_instructions = self.instructions or site_ins or "Choose the correct decision."

        payload = {
            "engine": self.name,
            "policy_model_id": self.backend.model_id,
            "policy_model_version": self.backend.model_version,
            "checkpoint_revision": self.backend.checkpoint_revision,
            "manifest_sha256": self.backend.manifest_sha256,
            "runtime_version": MODEL_RUNTIME_VERSION,
            "backend": self.backend.backend_name,
            "checkpoint": self.checkpoint,
            "manifest": self._manifest(self.checkpoint),
            "model": self.backend.model_id,
            "question": {
                "type": "choice",
                "criteria": list(site.choices),
                "instructions": chosen_instructions,
            },
        }
        self.predict(payload, rows[0]["state"])
        return payload

    def predict(self, payload, state):
        with self._lock:
            return self._predict(payload, state)

    def _predict(self, payload, state):
        from .model import RUNTIME_VERSION

        path = payload["checkpoint"]
        key = (path, canonical(payload["manifest"]), str(payload.get("runtime_version")))

        if key not in self._agents:
            rt_ver = str(payload.get("runtime_version", ""))
            # Support canonical format version "2" as well as legacy runtime version string
            if rt_ver not in (MODEL_RUNTIME_VERSION, "2", "2.0") and rt_ver != RUNTIME_VERSION:
                raise ValueError("Ink runtime version changed; recompile and requalify")
            if self._manifest(path) != payload["manifest"]:
                raise ValueError("Ink checkpoint integrity mismatch")

            # Check if this payload uses large or small
            p_model = payload.get("policy_model_id") or payload.get("model")
            if p_model == INK_DECISION_LARGE:
                backend = InkDecisionLargeBackend(path)
            else:
                backend = InkDecisionSmallBackend(path)
            backend.load()
            self._agents[key] = backend

        backend = self._agents[key]
        q_def = payload["question"]
        choices = q_def["criteria"]
        instructions = q_def.get("instructions")

        proposal = backend.propose(state, choices, instructions)
        choice = proposal.choice
        probability = proposal.scores.get(choice, proposal.confidence)

        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("Invalid engine probability")
        return choice, probability


# Canonical and backwards-compatible engine aliases
DecisionModelEngine = PolicyModelEngine
MlxDecisionEngine = PolicyModelEngine


class LinearClassifierEngine:
    """Lightweight regularized linear classifier (<0.02ms inference, ~50KB)."""

    name = "linear"

    def __init__(self, lambda_reg: float = 1.0, max_features: int = 1000):
        self.lambda_reg = lambda_reg
        self.max_features = max_features

    def compile(
        self, site, rows, representation_layer=None, *,
        representation_strategy="structured_lexical", policy_vectors=None,
    ):
        import time

        import numpy as np

        from .representation import RepresentationStrategy, StateRepresentationLayer

        rep = representation_layer or StateRepresentationLayer(site.state_schema).fit_from_records(rows)
        choices = list(site.choices)
        choice_to_idx = {c: i for i, c in enumerate(choices)}

        strategy = RepresentationStrategy(representation_strategy)
        policy_vectors = policy_vectors or {}
        X_rows = []
        Y_rows = []
        for r in rows:
            rep_state = rep.represent(r["state"])
            if strategy.uses_small:
                raw = policy_vectors.get(canonical(r["state"]))
                if raw is None:
                    raise ValueError("Small representation missing for verified training state")
                vec = np.array(raw, dtype=np.float32)
            else:
                vec = np.array(rep_state.semantic_vector, dtype=np.float32)
            struct_vals = [v for k, v in sorted(rep_state.structured_features.items())]
            if strategy.uses_structured and struct_vals:
                feat = np.concatenate([vec, np.array(struct_vals, dtype=np.float32)])
            else:
                feat = vec
            X_rows.append(feat)
            Y_rows.append(choice_to_idx.get(r["choice"], 0))

        X = np.stack(X_rows)
        N, D = X.shape
        X_b = np.hstack([X, np.ones((N, 1), dtype=np.float32)])

        K = len(choices)
        W = np.zeros((D + 1, K), dtype=np.float32)

        reg_matrix = self.lambda_reg * np.eye(D + 1, dtype=np.float32)
        reg_matrix[-1, -1] = 0.01  # Lighter penalty on bias
        A = X_b.T @ X_b + reg_matrix

        for k in range(K):
            y_k = np.where(np.array(Y_rows) == k, 1.0, -1.0).astype(np.float32)
            b = X_b.T @ y_k
            try:
                w_k = np.linalg.solve(A, b)
            except np.linalg.LinAlgError:
                w_k = np.linalg.pinv(A) @ b
            W[:, k] = w_k

        payload = {
            "engine": self.name,
            "choices": choices,
            "weights": W.tolist(),
            "representation": rep.to_dict(),
            "representation_strategy": strategy.value,
            "policy_vectors": policy_vectors if strategy.uses_small else {},
            "n_samples": N,
            "dim": D,
            "created": time.time(),
        }
        return payload

    def predict(self, payload, state):
        import numpy as np

        from .representation import RepresentationStrategy, StateRepresentationLayer

        rep = StateRepresentationLayer.from_dict(payload["representation"])
        rep_state = rep.represent(state)
        strategy = RepresentationStrategy(
            payload.get("representation_strategy", "structured_lexical")
        )
        if strategy.uses_small:
            raw = payload.get("policy_vectors", {}).get(canonical(state))
            if raw is None:
                raise ValueError("State has no compiled Small representation")
            vec = np.array(raw, dtype=np.float32)
        else:
            vec = np.array(rep_state.semantic_vector, dtype=np.float32)
        struct_vals = [v for k, v in sorted(rep_state.structured_features.items())]
        if strategy.uses_structured and struct_vals:
            feat = np.concatenate([vec, np.array(struct_vals, dtype=np.float32)])
        else:
            feat = vec

        x_b = np.append(feat, 1.0)
        W = np.array(payload["weights"], dtype=np.float32)
        logits = x_b @ W

        shifted = logits - np.max(logits)
        exp_scores = np.exp(shifted)
        probs = exp_scores / np.sum(exp_scores)

        top_idx = int(np.argmax(probs))
        choice = payload["choices"][top_idx]
        prob = float(probs[top_idx])
        return choice, prob


def get_engine_metadata(engine_name: str, payload: dict | None = None) -> dict[str, Any]:
    if engine_name == "exact":
        return {
            "name": "exact",
            "tier": "Serving Tier 1: Exact Hash Fast Path",
            "p50_latency_ms": 0.002,
            "memory_mb": 0.05,
            "complexity": "O(1) table lookup",
            "hardware": "CPU",
            "samples": len(payload.get("table", {})) if payload else 0,
        }
    elif engine_name in ("linear", "classifier"):
        return {
            "name": "linear",
            "tier": "Serving Tier 2: Compact Linear Local Engine",
            "p50_latency_ms": 0.02,
            "memory_mb": 0.1,
            "complexity": "O(D) linear projection",
            "hardware": "CPU",
            "samples": payload.get("n_samples", 0) if payload else 0,
        }
    elif engine_name in ("decision", INK_DECISION_SMALL, INK_DECISION_LARGE, LEGACY_INK_DECISION_V1):
        is_large = (
            engine_name == INK_DECISION_LARGE
            or (payload and payload.get("policy_model_id") == INK_DECISION_LARGE)
        )
        if is_large:
            return {
                "name": INK_DECISION_LARGE,
                "policy_model_id": INK_DECISION_LARGE,
                "tier": "Offline Training: High-Capability Teacher Model (DeBERTa-v3-large)",
                "p50_latency_ms": 196.0,
                "memory_mb": 1946.0,
                "complexity": "DeBERTa-v3-large Transformer encoder + schema heads",
                "hardware": "Apple Silicon CPU/MPS / Linux CPU",
                "samples": payload.get("n_samples", 0) if payload else 0,
            }
        return {
            "name": INK_DECISION_SMALL,
            "policy_model_id": INK_DECISION_SMALL,
            "tier": "Control Plane: Neural ModernBERT Distilled Proposal Engine",
            "p50_latency_ms": 48.0,
            "memory_mb": 800.0,
            "complexity": "ModernBERT-large Transformer encoder + distilled DecisionHead",
            "hardware": "Apple Silicon GPU / Linux CPU",
            "samples": payload.get("n_samples", 0) if payload else 0,
        }
    return {"name": engine_name, "p50_latency_ms": 1.0, "memory_mb": 1.0}


ENGINE_ALIASES = {
    INK_DECISION_SMALL: "decision",
    INK_DECISION_LARGE: "decision",
    LEGACY_INK_DECISION_V1: "decision",
    LEGACY_DECISION_V1: "decision",
    "classifier": "linear",
    "logistic": "linear",
}


def resolve_engine_key(key: str) -> str:
    """Map model identity keys onto the current engine key."""
    return ENGINE_ALIASES.get(key, key)
