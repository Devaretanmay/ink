"""Explicit, atomic provisioning of Ink's canonical Policy Models."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from .constants import (
    INK_DECISION_LARGE,
    INK_DECISION_SMALL,
    LEGACY_INK_DECISION_V1,
    resolve_policy_model_id,
)
from .policy_registry import get_model_spec


def specification(root=None, model_id=INK_DECISION_SMALL):
    """Return the integrity manifest belonging to a checkpoint.

    The shipped base checkpoint is described by checkpoint.json. An Ink-
    trained or installed checkpoint carries its own ink-model.json, whose
    hashes must be used instead of the base model hashes.
    """
    resolved_id = resolve_policy_model_id(model_id) or INK_DECISION_SMALL
    if root is not None:
        manifest = Path(root) / "ink-model.json"
        if manifest.is_file():
            payload = json.loads(manifest.read_text())
            if not isinstance(payload.get("sha256"), dict) or not payload["sha256"]:
                raise ValueError("Ink checkpoint manifest has no file hashes")
            if payload.get("weights_modified") is False:
                # Older installed copies used ink-model.json but predate the
                # trained-model version field. Accept only exact file-hash
                # matches with the current pinned manifest.
                base = json.loads(Path(__file__).with_name("checkpoint.json").read_text())
                if (
                    payload.get("name") in (base.get("name"), LEGACY_INK_DECISION_V1)
                    and payload.get("sha256") == base.get("sha256")
                ):
                    return base
                raise ValueError(
                    "legacy base checkpoint manifest does not match the pinned base model"
                )
            model_name = payload.get("name") or payload.get("model_id")
            model_ver = payload.get("version") or payload.get("model_version")
            if not model_name or not model_ver:
                raise ValueError("Ink checkpoint manifest has no model identity")
            return payload
    if resolved_id == INK_DECISION_LARGE:
        return get_model_spec(INK_DECISION_LARGE).to_dict()
    return json.loads(Path(__file__).with_name("checkpoint.json").read_text())


def model_path(model_id=INK_DECISION_SMALL):
    """Return the canonical filesystem path for the specified policy model."""
    resolved_id = resolve_policy_model_id(model_id) or INK_DECISION_SMALL
    if resolved_id == INK_DECISION_LARGE:
        large_override = os.environ.get("INK_LARGE_MODEL_DIR")
        if large_override:
            return Path(large_override).expanduser().resolve()
        return Path("~/.cache/ink/models/ink-decision-large").expanduser().resolve()

    small_override = os.environ.get("INK_SMALL_MODEL_DIR") or os.environ.get("INK_MODEL_DIR")
    if small_override:
        return Path(small_override).expanduser().resolve()
    return Path("~/.cache/ink/models/ink-decision-small").expanduser().resolve()


def _safe_specification(root=None, model_id=INK_DECISION_SMALL):
    try:
        return specification(root, model_id=model_id)
    except TypeError:
        return specification(root)


def _safe_verify(root, model_id=INK_DECISION_SMALL):
    try:
        return verify(root, model_id=model_id)
    except TypeError:
        return verify(root)


def verify(root, model_id=INK_DECISION_SMALL):
    """Verify integrity of files in the given checkpoint directory."""
    root = Path(root)
    spec = _safe_specification(root, model_id=model_id)
    for name, expected in spec["sha256"].items():
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(f"Model file missing: {name}; run ink model-install")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError(f"Model integrity mismatch: {name}")
    return root


def _link_or_copy(src: Path, dst: Path):
    """Link a file to avoid duplicate disk usage; fall back to copy across filesystems."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except (OSError, NotImplementedError):
        shutil.copyfile(src, dst)


def _migrate_legacy_model_cache(destination, model_id=INK_DECISION_SMALL):
    """Safely migrate legacy caches (decision-v1 or microloop) without duplicating 800MB weights."""
    resolved_id = resolve_policy_model_id(model_id) or INK_DECISION_SMALL
    if resolved_id != INK_DECISION_SMALL:
        return False
    if os.environ.get("INK_MODEL_DIR") or os.environ.get("INK_SMALL_MODEL_DIR"):
        return False
    if destination.exists() and (destination / "model.safetensors").is_file():
        return False

    legacy_candidates = [
        Path("~/.cache/ink/models/decision-v1").expanduser(),
        Path("~/.cache/microloop/models/decision-v1").expanduser(),
    ]
    legacy = None
    for cand in legacy_candidates:
        if (cand / "model.safetensors").is_file():
            legacy = cand
            break
    if legacy is None:
        return False

    spec = _safe_specification(model_id=INK_DECISION_SMALL)
    legacy_manifest = legacy / "ink-model.json"
    if not legacy_manifest.is_file():
        legacy_manifest = legacy / "microloop-model.json"
    if legacy_manifest.is_file():
        try:
            old_spec = json.loads(legacy_manifest.read_text())
            if old_spec.get("weights_modified") is True:
                old_spec["name"] = INK_DECISION_SMALL
                old_spec["model_id"] = INK_DECISION_SMALL
                if old_spec.get("base_model") and old_spec.get("base_revision"):
                    old_spec["base_checkpoint"] = (
                        f"{old_spec['base_model']}@{old_spec['base_revision']}"
                    )
                spec = old_spec
        except Exception:
            pass

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".ink-model-migration-", dir=destination.parent))
    try:
        for name in spec["sha256"]:
            source_file = legacy / name
            if not source_file.is_file():
                continue
            target_file = staging / name
            _link_or_copy(source_file, target_file)
        (staging / "ink-model.json").write_text(json.dumps(spec, indent=2) + "\n")
        _safe_verify(staging, model_id=INK_DECISION_SMALL)
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return True


def install(source=None, model_id=INK_DECISION_SMALL):
    """Download or install checkpoint on explicit setup; validate before exposing."""
    resolved_id = resolve_policy_model_id(model_id) or INK_DECISION_SMALL
    destination = model_path(resolved_id)
    if destination.exists() and (destination / "model.safetensors").is_file():
        return _safe_verify(destination, model_id=resolved_id)

    spec = _safe_specification(source, model_id=resolved_id)
    if source is None:
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            msg = (
                "huggingface-hub is not installed; reinstall ink: "
                "pip install --force-reinstall ink-jit"
            )
            raise RuntimeError(msg) from exc

        source = snapshot_download(
            spec["repository"],
            revision=spec["revision"],
            allow_patterns=list(spec["sha256"]),
        )
    source = _safe_verify(source, model_id=resolved_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{resolved_id}-", dir=destination.parent))
    try:
        for name in spec["sha256"]:
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(source) / name, target)
        (staging / "ink-model.json").write_text(json.dumps(spec, indent=2) + "\n")
        _safe_verify(staging, model_id=resolved_id)
        try:
            staging.rename(destination)
        except OSError:
            if not destination.exists():
                raise
            _safe_verify(destination, model_id=resolved_id)  # Another process may have completed first.
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return destination


def ensure_installed(auto_download=False, model_id=INK_DECISION_SMALL):
    """Verify a provisioned model without initiating network downloads."""
    resolved_id = resolve_policy_model_id(model_id) or INK_DECISION_SMALL
    destination = model_path(resolved_id)
    _migrate_legacy_model_cache(destination, model_id=resolved_id)
    if (destination / "model.safetensors").is_file():
        return _safe_verify(destination, model_id=resolved_id)
    if auto_download:
        return install(model_id=resolved_id)
    msg = f"Ink model is missing at {destination}; run 'ink model-install'"
    raise FileNotFoundError(msg)
