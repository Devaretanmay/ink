import os
import sys
from dataclasses import asdict
from unittest.mock import patch

import pytest
from ink import (
    DecisionSite,
    FallbackResult,
    Ink,
    Outcome,
    PromotionRequirements,
)
from ink.internal.engines import (
    DecisionEngine,
    DecisionModelEngine,
)

REQ = PromotionRequirements(10, 0.5, 0.5, 0.6, 0.25, 5, 100)


def verify_fn(state, choice):
    expected = "allow" if state.get("refund") else "deny"
    return Outcome(float(choice == expected), "verifier", "1", {"expected": expected})


def _feed(loop, site, count, prefix="t"):
    for i in range(count):
        state = {"refund": i % 2 == 0}
        expected = "allow" if state["refund"] else "deny"
        res = loop.decide(
            site=site,
            state=state,
            task_id=f"{prefix}-{i}",
            fallback=lambda exp=expected: FallbackResult(exp, model_calls=1),
        )
        outcome = verify_fn(state, res.choice)
        loop.record_outcome(res.decision_id, **asdict(outcome))


# --- Test 1: pip install ink-jit includes model engine by default ---
def test_default_install_includes_model_engine():
    with Ink(":memory:") as loop:
        assert "decision" in loop.engines
        assert isinstance(loop.engines["decision"], DecisionModelEngine)
        assert isinstance(loop.engines["decision"], DecisionEngine)


# --- Test 2: model_enabled=True is the default ---
def test_model_enabled_default_true():
    with Ink(":memory:") as loop:
        assert loop._model_enabled is True


# --- Test 3: model_enabled=False disables decision engine ---
def test_model_disabled_excludes_decision_engine():
    with Ink(":memory:", model_enabled=False) as loop:
        assert loop._model_enabled is False
        assert "decision" not in loop.engines
        assert "exact" in loop.engines


# --- Test 4: INK_MODEL_DISABLED=1 env var ---
def test_env_var_disables_model():
    with patch.dict(os.environ, {"INK_MODEL_DISABLED": "1"}):
        with Ink(":memory:") as loop:
            assert loop._model_enabled is False
            assert "decision" not in loop.engines


# --- Test 5: INK_MODEL_DISABLED=true env var ---
def test_env_var_disables_model_true_string():
    with patch.dict(os.environ, {"INK_MODEL_DISABLED": "true"}):
        with Ink(":memory:") as loop:
            assert loop._model_enabled is False


# --- Test 6: env var overrides explicit model_enabled=True ---
def test_env_var_overrides_constructor():
    with patch.dict(os.environ, {"INK_MODEL_DISABLED": "1"}):
        with Ink(":memory:", model_enabled=True) as loop:
            assert loop._model_enabled is False
            assert "decision" not in loop.engines


# --- Test 7: model_enabled=False still allows exact engine operation ---
def test_exact_engine_works_without_model(tmp_path):
    db = tmp_path / "exact_only.db"
    site = DecisionSite("test.route", {"refund": "boolean"}, ("allow", "deny"))
    with Ink(str(db), model_enabled=False) as loop:
        loop.register(site)
        _feed(loop, site, 250, "obs")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        _feed(loop, site, 100, "shadow")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        assert loop.inspect(site)["state"] == "ACTIVE"
        served = []
        for _ in range(15):
            res = loop.decide(
                site=site,
                state={"refund": True},
                fallback=lambda: FallbackResult("deny", model_calls=1),
            )
            served.append(res)
        assert any(r.source == "fast_path" for r in served)


# --- Test 8: exact qualified fast path does NOT invoke model engine ---
def test_exact_fast_path_bypasses_model_inference(tmp_path):
    db = tmp_path / "bypass.db"
    site = DecisionSite("test.route", {"refund": "boolean"}, ("allow", "deny"))
    with Ink(str(db)) as loop:
        loop.register(site)
        _feed(loop, site, 250, "obs")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        _feed(loop, site, 100, "shadow")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        decision_engine = loop.engines.get("decision")
        assert decision_engine is not None
        assert decision_engine._agents == {}
        for _ in range(20):
            loop.decide(
                site=site,
                state={"refund": True},
                fallback=lambda: FallbackResult("deny", model_calls=1),
            )
        assert decision_engine._agents == {}


# --- Test 9: DecisionModelEngine construction does NOT load weights ---
def test_model_engine_lazy_construction():
    engine = DecisionModelEngine()
    assert engine._agents == {}
    assert engine.name == "decision"


# --- Test 10: inspect() exposes model state ---
def test_inspect_exposes_model_state():
    with Ink(":memory:") as loop:
        site = DecisionSite("test.route", {"x": "integer"}, ("a", "b"))
        loop.register(site)
        _feed_simple(loop, site, 10)
        info = loop.inspect(site)
        assert "model" in info
        assert info["model"]["enabled"] is True
        assert info["model"]["loaded"] is False
        assert info["model"]["implementation"] == "decision"


# --- Test 11: inspect() with model_enabled=False ---
def test_inspect_model_disabled():
    with Ink(":memory:", model_enabled=False) as loop:
        site = DecisionSite("test.route", {"x": "integer"}, ("a", "b"))
        loop.register(site)
        _feed_simple(loop, site, 10)
        info = loop.inspect(site)
        assert info["model"]["enabled"] is False
        assert info["model"]["implementation"] is None


# --- Test 12: status() includes model_enabled ---
def test_status_includes_model_enabled():
    with Ink(":memory:") as loop:
        site = DecisionSite("test.route", {"x": "integer"}, ("a", "b"))
        loop.register(site)
        _feed_simple(loop, site, 10)
        st = loop.status(site)
        assert "model_enabled" in st
        assert st["model_enabled"] is True


# --- Test 13: status() model_enabled=False ---
def test_status_model_disabled():
    with Ink(":memory:", model_enabled=False) as loop:
        site = DecisionSite("test.route", {"x": "integer"}, ("a", "b"))
        loop.register(site)
        _feed_simple(loop, site, 10)
        st = loop.status(site)
        assert st["model_enabled"] is False


# --- Test 14: globally disabled + model_enabled interaction ---
def test_globally_disabled_with_model_enabled():
    with Ink(":memory:", disabled=True, model_enabled=True) as loop:
        site = DecisionSite("test.route", {"x": "integer"}, ("a", "b"))
        res = loop.decide(
            site=site,
            state={"x": 1},
            fallback=lambda: "a",
        )
        assert res.source == "fallback"
        assert res.fallback_reason == "globally_disabled"


# --- Test 15: legacy engine alias resolution with model enabled ---
def test_legacy_alias_resolution_model_enabled():
    with Ink(":memory:") as loop:
        assert "laya" in loop.engines
        assert "ink-decision-v1" in loop.engines
        assert loop.engines["laya"] is loop.engines["decision"]


# --- Test 16: legacy alias absent when model disabled ---
def test_legacy_alias_absent_model_disabled():
    with Ink(":memory:", model_enabled=False) as loop:
        assert "laya" not in loop.engines
        assert "ink-decision-v1" not in loop.engines
        assert "decision" not in loop.engines


# --- Test 17: Windows platform raises OSError from model engine ---
def test_windows_platform_model_restriction():
    from ink.internal.engines import _require_neural_dependencies
    with patch.object(sys, "platform", "win32"):
        with pytest.raises(OSError, match="Linux or macOS"):
            _require_neural_dependencies()


# --- Test 18: model_enabled=False full lifecycle (observe->active) works ---
def test_full_lifecycle_without_model(tmp_path):
    db = tmp_path / "no_model_life.db"
    site = DecisionSite("test.route", {"refund": "boolean"}, ("allow", "deny"))
    with Ink(str(db), model_enabled=False) as loop:
        loop.register(site)
        assert loop.status(site)["state"] == "OBSERVE"
        _feed(loop, site, 250, "obs")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        assert loop.status(site)["state"] == "SHADOW"
        _feed(loop, site, 100, "shadow")
        loop.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        assert loop.status(site)["state"] == "ACTIVE"


# --- Test 19: neural deps are now default (not optional) ---
def test_neural_deps_are_default():
    import importlib.util
    if sys.platform != "win32":
        assert importlib.util.find_spec("tokenizers") is not None, (
            "tokenizers should be installed by default"
        )
        assert importlib.util.find_spec("huggingface_hub") is not None, (
            "huggingface-hub should be installed by default"
        )


# --- Test 20: model engine error message does not mention [neural] ---
def test_error_message_no_optional_reference():
    from ink.internal.engines import _require_neural_dependencies
    with patch.dict("sys.modules", {"mlx": None, "mlx.core": None}):
        if sys.platform != "win32":
            with pytest.raises(RuntimeError, match="reinstall ink"):
                _require_neural_dependencies()


# --- Helper ---
def _feed_simple(loop, site, count):
    for i in range(count):
        res = loop.decide(
            site=site,
            state={"x": i % 2},
            task_id=f"s-{i}",
            fallback=lambda: "a",
        )
        loop.record_outcome(res.decision_id, quality=1.0, verifier="v", verifier_version="1")
