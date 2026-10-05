"""Provisioning must never expose partial or tampered model files."""

import hashlib
import inspect
import json

import pytest
from ink import Ink
from ink.internal.engines import DecisionModelEngine, resolve_engine_key
from ink.internal.model import RUNTIME_VERSION, registry


def test_neural_engine_is_default():
    assert inspect.signature(Ink.compile).parameters["engine"].default == "decision"
    assert inspect.signature(Ink.maintenance).parameters["engine"].default == "decision"
    assert DecisionModelEngine.name == "decision"
    assert RUNTIME_VERSION.startswith("ink-decision-")


def test_legacy_engine_key_resolves_to_integral_engine(tmp_path):
    assert resolve_engine_key("laya") == "decision"
    assert resolve_engine_key("ink-decision-v1") == "decision"
    assert resolve_engine_key("decision") == "decision"
    with Ink(":memory:") as client:
        assert client.engines["laya"] is client.engines["decision"]
        assert client.compile.__defaults__ is None or True


def test_atomic_offline_setup_and_tamper_rejection(tmp_path, monkeypatch):
    content = b"fixture for provisioning only, not neural inference"
    spec = {"name": "test", "sha256": {"encoder/config.json": hashlib.sha256(content).hexdigest()}}
    monkeypatch.setattr(registry, "specification", lambda root=None: spec)
    target = tmp_path / "installed"
    monkeypatch.setenv("INK_MODEL_DIR", str(target))
    source = tmp_path / "source"
    (source / "encoder").mkdir(parents=True)
    (source / "encoder/config.json").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        registry.install(source)
    assert not target.exists()
    (source / "encoder/config.json").write_bytes(content)
    assert registry.install(source) == target
    assert json.loads((target / "ink-model.json").read_text()) == spec
    assert registry.install(source) == target
    (target / "encoder/config.json").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        registry.install(source)


def test_trained_checkpoint_uses_its_own_manifest(tmp_path, monkeypatch):
    weights = b"trained checkpoint fixture"
    config = b"trained config fixture"
    source = tmp_path / "trained"
    source.mkdir()
    (source / "model.safetensors").write_bytes(weights)
    (source / "rl_agent_config.json").write_bytes(config)
    manifest = {
        "name": "ink-decision-v1",
        "version": "1.0.0",
        "weights_modified": True,
        "sha256": {
            "model.safetensors": hashlib.sha256(weights).hexdigest(),
            "rl_agent_config.json": hashlib.sha256(config).hexdigest(),
        },
    }
    (source / "ink-model.json").write_text(json.dumps(manifest))
    target = tmp_path / "app-support" / "model"
    monkeypatch.setenv("INK_MODEL_DIR", str(target))

    assert registry.verify(source) == source
    assert registry.install(source) == target
    assert registry.specification(target) == manifest

    (target / "model.safetensors").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        registry.verify(target)


def test_legacy_base_manifest_without_version_uses_only_pinned_hashes(tmp_path):
    base = registry.specification()
    fields = ("name", "upstream", "revision", "weights_modified", "sha256")
    legacy = {key: base[key] for key in fields}
    legacy["weights_modified"] = False
    (tmp_path / "ink-model.json").write_text(json.dumps(legacy))

    assert registry.specification(tmp_path) == base
    legacy["sha256"]["model.safetensors"] = "0" * 64
    (tmp_path / "ink-model.json").write_text(json.dumps(legacy))
    with pytest.raises(ValueError, match="does not match the pinned base"):
        registry.specification(tmp_path)


def test_runtime_model_check_never_downloads_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("INK_MODEL_DIR", str(tmp_path / "missing"))
    monkeypatch.setenv("INK_AUTO_INSTALL", "1")
    with pytest.raises(FileNotFoundError, match="model is missing"):
        registry.ensure_installed(auto_download=False)


def test_interrupted_setup_leaves_no_model(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "weights").write_bytes(b"weights")
    monkeypatch.setattr(
        registry,
        "specification",
        lambda root=None: {"sha256": {"weights": hashlib.sha256(b"weights").hexdigest()}},
    )
    target = tmp_path / "installed"
    monkeypatch.setenv("INK_MODEL_DIR", str(target))

    def interrupted(*args):
        raise OSError("interrupted")

    monkeypatch.setattr(registry.shutil, "copyfile", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        registry.install(source)
    assert not target.exists()
    assert not list(tmp_path.glob(".decision-v1-*"))


def test_default_model_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("INK_MODEL_DIR", str(tmp_path))
    assert DecisionModelEngine().checkpoint == str(tmp_path)


def test_default_model_cache_migrates_from_legacy_name(tmp_path, monkeypatch):
    weights = b"unchanged model bytes"
    digest = hashlib.sha256(weights).hexdigest()
    spec = {"name": "ink-decision-v1", "sha256": {"model.safetensors": digest}}
    monkeypatch.setattr(registry, "specification", lambda root=None: spec)
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy = tmp_path / ".cache/microloop/models/decision-v1"
    legacy.mkdir(parents=True)
    (legacy / "model.safetensors").write_bytes(weights)
    (legacy / "microloop-model.json").write_text(
        json.dumps({
            "name": "microloop-decision-v1",
            "version": "1.0.0",
            "weights_modified": True,
            "trained_by": "legacy-trainer",
            "base_model": "upstream/model",
            "base_revision": "revision",
            "base_checkpoint": "/old/developer/path",
            "sha256": {"model.safetensors": digest},
        })
    )

    target = registry.ensure_installed()
    assert target == tmp_path / ".cache/ink/models/decision-v1"
    assert (target / "model.safetensors").read_bytes() == weights
    assert (target / "ink-model.json").is_file()
    migrated_spec = json.loads((target / "ink-model.json").read_text())
    assert migrated_spec["name"] == "ink-decision-v1"
    assert migrated_spec["base_checkpoint"] == "upstream/model@revision"
    assert (legacy / "model.safetensors").read_bytes() == weights


def test_previous_runtime_requires_requalification(tmp_path):
    engine = DecisionModelEngine(tmp_path)
    with pytest.raises(ValueError, match="runtime version changed"):
        engine.predict(
            {
                "checkpoint": str(tmp_path),
                "manifest": {},
                "runtime_version": "0.2.0",
                "question": {},
            },
            {},
        )
