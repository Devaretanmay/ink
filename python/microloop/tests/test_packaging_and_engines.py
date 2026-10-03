from unittest.mock import patch

import microloop
import pytest
from microloop.internal.engines import DecisionModelEngine, ExactEngine
from microloop.internal.model.registry import install
from microloop.internal.model.training import finetune


def test_version_exported():
    assert isinstance(microloop.__version__, str)
    assert len(microloop.__version__) > 0


def test_exact_engine_standalone():
    engine = ExactEngine()
    rows = [
        {"state": {"action": "read"}, "choice": "allow"},
        {"state": {"action": "read"}, "choice": "allow"},
        {"state": {"action": "write"}, "choice": "deny"},
    ]
    compiled = engine.compile(site=None, rows=rows)
    assert compiled["engine"] == "exact"
    choice, prob = engine.predict(compiled, {"action": "read"})
    assert choice == "allow"
    assert prob == 1.0


def test_decision_engine_requires_neural_dependencies():
    engine = DecisionModelEngine()
    with patch.dict("sys.modules", {"mlx.core": None}):
        with pytest.raises(RuntimeError, match="reinstall microloop"):
            engine.compile(site=None, rows=[])


def test_registry_install_requires_neural_dependencies(tmp_path):
    with patch("microloop.internal.model.registry.model_path", return_value=tmp_path / "missing"):
        with patch.dict("sys.modules", {"huggingface_hub": None}):
            with pytest.raises(RuntimeError, match="reinstall microloop"):
                install(source=None)


def test_training_finetune_requires_neural_dependencies():
    with patch.dict("sys.modules", {"mlx.core": None}):
        with pytest.raises(RuntimeError, match="reinstall microloop"):
            finetune(None, [{"state": {}, "choices": ["a"], "choice": "a"}], "/tmp/test_out")
