"""Private local engine boundary."""

from __future__ import annotations

import hashlib
import math
import sys
import threading
from collections import Counter
from pathlib import Path
from typing import Protocol, runtime_checkable

from .contracts import canonical


def _require_neural_dependencies():
    if sys.platform == "win32":
        raise OSError(
            "ink-decision-v1 requires Linux or macOS (MLX has no Windows build); "
            "use engine='exact' on Windows"
        )
    try:
        import mlx.core  # noqa: F401
    except ImportError as exc:
        msg = (
            "MLX is not installed; reinstall ink: "
            "pip install --force-reinstall ink"
        )
        raise RuntimeError(msg) from exc


@runtime_checkable
class DecisionEngine(Protocol):
    name: str

    def compile(self, site, rows) -> dict: ...
    def predict(self, payload: dict, state: dict) -> tuple[str, float]: ...


class ExactEngine:
    """Ultra-fast qualified execution tier for proven repeated states.

    Acts as an optimized compiler branch bypassing full model inference
    when exact state behavior has achieved sufficient qualification evidence.
    """

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


class DecisionModelEngine:
    """Concrete MLX neural implementation of Ink's internal learned decision model.

    Generates candidate choice predictions for the Decision Engine. Candidate
    predictions have zero serving authority until independently qualified.
    """

    name = "decision"

    LEGACY_KEYS = ("ink-decision-v1",)

    def __init__(self, checkpoint=None, *, instructions=None):
        from .model.registry import model_path

        self.checkpoint = (
            str(Path(checkpoint).expanduser().resolve()) if checkpoint else str(model_path())
        )
        self._managed_checkpoint = checkpoint is None
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
        from .model import RUNTIME_VERSION
        from .model.registry import ensure_installed, verify

        if self._managed_checkpoint:
            ensure_installed(auto_download=False)
            verify(self.checkpoint)
        examples = []
        seen = set()
        for row in rows:
            key = canonical(row["state"])
            if key not in seen:
                examples.append({"state": row["state"], "choice": row["choice"]})
                seen.add(key)
            if len(examples) == 3:
                break
        payload = {
            "engine": self.name,
            "checkpoint": self.checkpoint,
            "manifest": self._manifest(self.checkpoint),
            "runtime_version": RUNTIME_VERSION,
            "model": "ink-decision-v1",
            "question": {
                "type": "choice",
                "criteria": list(site.choices),
                "instructions": self.instructions
                or ("Choose the next action. Observed examples: " + canonical(examples)),
            },
        }
        self.predict(payload, rows[0]["state"])
        return payload

    def predict(self, payload, state):
        with self._lock:
            return self._predict(payload, state)

    def _predict(self, payload, state):
        _require_neural_dependencies()
        from .model import RUNTIME_VERSION
        from .model.agent import load
        path = payload["checkpoint"]
        key = (path, canonical(payload["manifest"]), payload["runtime_version"])
        if key not in self._agents:
            if RUNTIME_VERSION != payload["runtime_version"]:
                raise ValueError("Ink runtime version changed; recompile and requalify")
            if self._manifest(path) != payload["manifest"]:
                raise ValueError("Ink checkpoint integrity mismatch")
            self._agents[key] = load(path)
        answer = self._agents[key].predict(state, {"decision": payload["question"]})["answers"][
            "decision"
        ]
        choice = answer["choice"]
        probability = answer["probabilities"][choice]
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("Invalid engine probability")
        return choice, probability


MlxDecisionEngine = DecisionModelEngine
ENGINE_ALIASES = {"ink-decision-v1": "decision"}


def resolve_engine_key(key):
    """Map model identity keys onto the current engine key."""
    return ENGINE_ALIASES.get(key, key)
