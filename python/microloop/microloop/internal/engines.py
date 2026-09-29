"""Private local engine boundary. Engines produce proposals, never business actions."""

from __future__ import annotations

import hashlib
import math
import sys
import threading
from collections import Counter
from pathlib import Path
from typing import Protocol

from .contracts import canonical


def _require_neural_platform():
    if sys.platform == "win32":
        raise OSError(
            "microloop-decision-v1 requires Linux or macOS (MLX has no Windows build); "
            "use engine='exact' on Windows"
        )


class DecisionEngine(Protocol):
    name: str

    def compile(self, site, rows) -> dict: ...
    def predict(self, payload: dict, state: dict) -> tuple[str, float]: ...


class ExactEngine:
    """Portable learned frequency table; works on every platform including Windows."""

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
    """Integral Microloop Decision v1 neural engine.

    The inference code is vendored under ``microloop.internal.model`` and the
    checkpoint is provisioned via ``microloop model-install``. Scores remain
    uncalibrated until independent outcome qualification.
    """

    name = "decision"

    # Stored artifacts written before the rename carry ``engine: "laya"``.
    LEGACY_KEYS = ("laya", "microloop-decision-v1")

    def __init__(self, checkpoint=None, *, instructions=None):
        _require_neural_platform()
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
                "Microloop model is missing or incomplete; run microloop model-install"
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
        from .model import RUNTIME_VERSION
        from .model.registry import verify

        if self._managed_checkpoint:
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
            "model": "microloop-decision-v1",
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
        from .model import RUNTIME_VERSION
        from .model.agent import load

        _require_neural_platform()
        path = payload["checkpoint"]
        key = (path, canonical(payload["manifest"]), payload["runtime_version"])
        if key not in self._agents:
            if RUNTIME_VERSION != payload["runtime_version"]:
                raise ValueError("Microloop runtime version changed; recompile and requalify")
            if self._manifest(path) != payload["manifest"]:
                raise ValueError("Microloop checkpoint integrity mismatch")
            self._agents[key] = load(path)
        answer = self._agents[key].predict(state, {"decision": payload["question"]})["answers"][
            "decision"
        ]
        choice = answer["choice"]
        probability = answer["probabilities"][choice]
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("Invalid engine probability")
        return choice, probability


# Private compatibility alias for existing integrations. Stored artifact keys
# "laya" and "microloop-decision-v1" resolve to the integral decision engine.
LayaEngine = DecisionModelEngine
ENGINE_ALIASES = {"laya": "decision", "microloop-decision-v1": "decision"}


def resolve_engine_key(key):
    """Map historical engine keys onto the current integral key."""
    return ENGINE_ALIASES.get(key, key)
