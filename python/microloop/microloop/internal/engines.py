"""Private local engine boundary. Engines produce proposals, never business actions."""

from __future__ import annotations

import hashlib
import importlib.metadata
import math
import threading
from collections import Counter
from pathlib import Path
from typing import Protocol

from .contracts import canonical


class DecisionEngine(Protocol):
    name: str

    def compile(self, site, rows) -> dict: ...
    def predict(self, payload: dict, state: dict) -> tuple[str, float]: ...


class ExactEngine:
    """Portable learned frequency table; deliberately not marketed as Laya."""

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


class LayaEngine:
    """Laya-MLX with a local checkpoint and an example-configured typed question.

    Feasibility: optional package ``laya-mlx==0.2.0`` (darwin arm64, Python >=3.11,
    macOS 14+); interface ``laya_mlx.load(checkpoint)`` plus
    ``agent.predict(state, questions)``; artifact ``model.safetensors`` with
    ``rl_agent_config.json``, ``encoder/config.json``, tokenizer files.
    Upstream weights ``convaiinnovations/laya`` declare Apache-2.0; the MLX port
    is independent and the cached checkpoint ships no license file, so
    redistribution rights stay unverified. This configures a pretrained model;
    it does not claim to fine-tune its weights. The runtime clamps out-of-range
    temperatures, so raw scores stay uncalibrated; coverage and empirical
    confidence are learned separately from observed outcomes.
    """

    name = "laya"

    def __init__(self, checkpoint=None, *, instructions=None):
        self.checkpoint = str(Path(checkpoint).expanduser().resolve()) if checkpoint else None
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
            raise FileNotFoundError("Laya requires a complete local checkpoint and tokenizer")
        result = {}
        for file in required:
            h = hashlib.sha256()
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
            result[str(file.relative_to(root))] = h.hexdigest()
        return result

    def compile(self, site, rows):
        if not self.checkpoint:
            raise ValueError("Pass LayaEngine(checkpoint=...) with a local checkpoint")
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
            "runtime_version": importlib.metadata.version("laya-mlx"),
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
        import laya_mlx

        path = payload["checkpoint"]
        key = (path, canonical(payload["manifest"]), payload["runtime_version"])
        if key not in self._agents:
            if importlib.metadata.version("laya-mlx") != payload["runtime_version"]:
                raise ValueError("Laya runtime version changed; recompile and requalify")
            if self._manifest(path) != payload["manifest"]:
                raise ValueError("Laya checkpoint integrity mismatch")
            self._agents[key] = laya_mlx.load(path)
        answer = self._agents[key].predict(state, {"decision": payload["question"]})["answers"][
            "decision"
        ]
        choice = answer["choice"]
        probability = answer["probabilities"][choice]
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("Invalid engine probability")
        return choice, probability
