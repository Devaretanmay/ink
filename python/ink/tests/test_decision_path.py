"""Integral decision-engine candidate, restart serve, novel fallback.

Skips when MLX or a local checkpoint is unavailable.
Checkpoint: managed `ink model-install` dir, $INK_CHECKPOINT,
or the pinned base-checkpoint HF cache.
"""

import importlib.util
import json
import os
import time
from pathlib import Path

import pytest
from ink import DecisionSite, Ink, PromotionRequirements

SITE = DecisionSite("decision.path", {"request": "string"}, ("refund", "specialist"))
REQ = PromotionRequirements(6, 0.5, 0.5, 0.6, 0.25, 3, 50)

REFUND_TEXT = "I was billed twice. Please refund the duplicate charge."
SPECIALIST_TEXT = "Complex integration failure, need a specialist to investigate."


def _state(i):
    return {"request": REFUND_TEXT if i % 2 == 0 else SPECIALIST_TEXT}


def _expected(state):
    return "refund" if state["request"] == REFUND_TEXT else "specialist"


def _checkpoint():
    from ink.internal.model.registry import model_path

    if (model_path() / "model.safetensors").is_file():
        return str(model_path())
    env = os.environ.get("INK_CHECKPOINT")
    if env and Path(env).expanduser().exists():
        return str(Path(env).expanduser().resolve())
    base = Path("~/.cache/huggingface/hub/models--aac6fef--laya-mlx/snapshots").expanduser()
    if base.is_dir():
        for snap in sorted(base.iterdir()):
            if (snap / "model.safetensors").is_file():
                return str(snap.resolve())
    return None


def _verify(state, choice):
    from ink import Outcome

    expected = _expected(state)
    return Outcome(float(choice == expected), "ledger", "1", {"expected": expected})


needs_decision = pytest.mark.skipif(
    importlib.util.find_spec("mlx") is None or _checkpoint() is None,
    reason="MLX or local checkpoint unavailable",
)


@needs_decision
def test_decision_compile_persist_restart_serve(tmp_path):
    from ink.internal.engines import DecisionModelEngine

    checkpoint = _checkpoint()
    path = tmp_path / "decision.db"

    def run(client, count, prefix):
        for i in range(count):
            state = _state(i)
            result = client.decide(
                site=SITE,
                state=state,
                task_id=f"{prefix}-{i}",
                fallback=lambda state=state: _expected(state),
            )
            client.record_outcome(result.decision_id, **_verify(state, result.choice).__dict__)

    with Ink(path, engines=[DecisionModelEngine(checkpoint)]) as client:
        run(client, 200, "observe")
        artifact = client.compile(SITE)
        assert artifact
        client.calibrate(SITE, verifier=_verify, requirements=REQ)
        run(client, 60, "shadow")
        evidence = client.evaluate(SITE, verifier=_verify)
        assert evidence["qualified"]
        assert client.inspect(SITE)["state"] == "ACTIVE"

    # Restart: persisted artifact serves known states locally.
    with Ink(path, engines=[DecisionModelEngine(checkpoint)]) as client:
        assert client.inspect(SITE)["state"] == "ACTIVE"
        seen = {
            r.source
            for r in (
                client.decide(site=SITE, state=_state(0), fallback=lambda: "refund")
                for _ in range(20)
            )
        }
        assert "fast_path" in seen
        assert seen <= {"fast_path", "fallback"}
        # Novel typed state abstains to fallback; never invents coverage.
        novel = client.decide(
            site=SITE,
            state={"request": "Unseen one-offbilling questionXYZ"},
            fallback=lambda: "specialist",
        )
        assert novel.source == "fallback"
        assert novel.fallback_reason in ("observe", "outside_coverage")
        # Broken engine falls back without breaking the artifact.
        before = client._artifact(SITE.version)["checksum"]
        client.engines["decision"] = _Broken()
        broken = client.decide(site=SITE, state=_state(0), fallback=lambda: "refund")
        assert broken.source == "fallback"
        assert broken.fallback_reason == "engine_or_store_unavailable"
        assert client._artifact(SITE.version)["checksum"] == before


class _Broken:
    def predict(self, *args):
        raise RuntimeError("engine is down")


@needs_decision
def test_decision_path_benchmark(tmp_path):
    """Cold load, warm inference, artifact size, dispatcher overhead."""
    from ink.internal.engines import DecisionModelEngine

    checkpoint = _checkpoint()
    t0 = time.perf_counter()
    from ink.internal.model.agent import load

    agent = load(checkpoint)
    cold = time.perf_counter() - t0
    question = {
        "decision": {
            "type": "choice",
            "criteria": ["refund", "specialist"],
            "instructions": "Choose the next action.",
        }
    }
    t0 = time.perf_counter()
    agent.predict({"request": REFUND_TEXT}, question)
    warm = time.perf_counter() - t0
    size_mb = Path(checkpoint, "model.safetensors").stat().st_size / 1e6

    with Ink(tmp_path / "bench.db", engines=[DecisionModelEngine(checkpoint)]) as client:
        t0 = time.perf_counter()
        client.decide(site=SITE, state=_state(0), fallback=lambda: "refund")
        overhead = time.perf_counter() - t0
    report = {"cold_s": cold, "warm_s": warm, "size_mb": size_mb, "dispatch_s": overhead}
    (tmp_path / "decision_bench.json").write_text(json.dumps(report))
    assert cold > 0 and warm > 0 and overhead > 0
    assert size_mb > 100
