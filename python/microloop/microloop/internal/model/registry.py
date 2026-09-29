"""Explicit, atomic provisioning of Microloop's pinned pretrained model."""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


def specification():
    return json.loads(Path(__file__).with_name("checkpoint.json").read_text())


def model_path():
    return (
        Path(os.environ.get("MICROLOOP_MODEL_DIR", "~/.cache/microloop/models/decision-v1"))
        .expanduser()
        .resolve()
    )


def verify(root):
    root = Path(root)
    for name, expected in specification()["sha256"].items():
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(f"Model file missing: {name}; run microloop model-install")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError(f"Model integrity mismatch: {name}")
    return root


def install(source=None):
    """Download only on explicit setup; validate before exposing the directory."""
    destination = model_path()
    if destination.exists():
        return verify(destination)
    spec = specification()
    if source is None:
        from huggingface_hub import snapshot_download

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
        (staging / "microloop-model.json").write_text(json.dumps(spec, indent=2) + "\n")
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
