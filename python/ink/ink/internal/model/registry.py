"""Explicit, atomic provisioning of Ink's pinned pretrained model."""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


def specification(root=None):
    """Return the integrity manifest belonging to a checkpoint.

    The shipped base checkpoint is described by checkpoint.json. An Ink-
    trained checkpoint carries its own ink-model.json, whose hashes must
    be used instead of the base model hashes.
    """
    if root is not None:
        manifest = Path(root) / "ink-model.json"
        if manifest.is_file():
            payload = json.loads(manifest.read_text())
            if not isinstance(payload.get("sha256"), dict) or not payload["sha256"]:
                raise ValueError("Ink checkpoint manifest has no file hashes")
            if payload.get("weights_modified") is False:
                # Older installed copies of the upstream base checkpoint used
                # ink-model.json but predate the trained-model version
                # field. Accept only an exact identity/hash match with the
                # current pinned base manifest.
                base = json.loads(Path(__file__).with_name("checkpoint.json").read_text())
                identity_fields = ("name", "upstream", "revision", "sha256")
                if all(payload.get(key) == base.get(key) for key in identity_fields):
                    return base
                raise ValueError(
                    "legacy base checkpoint manifest does not match the pinned base model"
                )
            if not payload.get("name") or not payload.get("version"):
                raise ValueError("Ink checkpoint manifest has no model identity")
            return payload
    return json.loads(Path(__file__).with_name("checkpoint.json").read_text())


def model_path():
    return (
        Path(os.environ.get("INK_MODEL_DIR", "~/.cache/ink/models/decision-v1"))
        .expanduser()
        .resolve()
    )


def verify(root):
    root = Path(root)
    for name, expected in specification(root)["sha256"].items():
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


def _migrate_legacy_model_cache(destination):
    """Copy and re-identify the old default cache without deleting it."""
    if os.environ.get("INK_MODEL_DIR"):
        return False
    legacy = Path("~/.cache/microloop/models/decision-v1").expanduser()
    if destination.exists() or not (legacy / "model.safetensors").is_file():
        return False
    spec = specification()
    legacy_manifest = legacy / "microloop-model.json"
    if legacy_manifest.is_file():
        old_spec = json.loads(legacy_manifest.read_text())
        if old_spec.get("weights_modified") is True:
            old_spec["name"] = "ink-decision-v1"
            if old_spec.get("base_model") and old_spec.get("base_revision"):
                old_spec["base_checkpoint"] = (
                    f"{old_spec['base_model']}@{old_spec['base_revision']}"
                )
            spec = old_spec
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".ink-model-migration-", dir=destination.parent))
    try:
        for name in spec["sha256"]:
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(legacy / name, target)
        (staging / "ink-model.json").write_text(json.dumps(spec, indent=2) + "\n")
        verify(staging)
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return True


def install(source=None):
    """Download only on explicit setup; validate before exposing the directory."""
    destination = model_path()
    if destination.exists():
        return verify(destination)
    spec = specification(source)
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
            spec["upstream"],
            revision=spec["revision"],
            allow_patterns=list(spec["sha256"]),
        )
    source = verify(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".decision-v1-", dir=destination.parent))
    try:
        for name in spec["sha256"]:
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
        (staging / "ink-model.json").write_text(json.dumps(spec, indent=2) + "\n")
        verify(staging)
        try:
            staging.rename(destination)
        except OSError:
            if not destination.exists():
                raise
            verify(destination)  # Another setup process may have completed first.
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination


def ensure_installed(auto_download=False):
    """Verify a provisioned model without initiating network downloads."""
    destination = model_path()
    _migrate_legacy_model_cache(destination)
    if (destination / "model.safetensors").is_file():
        return verify(destination)
    if auto_download:
        return install()
    msg = f"Ink model is missing at {destination}; run 'ink model-install'"
    raise FileNotFoundError(msg)
