#!/usr/bin/env bash
# Native Linux validation through Docker; the checkout and model are read-only.
set -euo pipefail
project_root=$(cd "$(dirname "$0")/.." && pwd)
model_root=${MICROLOOP_MODEL_DIR:-"$HOME/.cache/microloop/models/decision-v1"}
output_root=${1:-"$project_root/.microloop/linux-validation-$(date +%Y%m%d-%H%M%S)"}
if [ ! -f "$model_root/model.safetensors" ]; then
    echo "Run microloop model-install first, or set MICROLOOP_MODEL_DIR." >&2
    exit 2
fi
mkdir -p "$output_root"
output_root=$(cd "$output_root" && pwd)
docker run -i --rm \
    -v "$project_root:/source:ro" -v "$model_root:/model:ro" \
    -v "$output_root:/output" -e MICROLOOP_MODEL_DIR=/model \
    rust:1.85-bookworm sh -s <<'CONTAINER'
set -eu
export PIP_CACHE_DIR=/output/pip-cache
export CARGO_HOME=/output/cargo-cache
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3-venv
python3 -m venv /tmp/validation-env
export PATH="/tmp/validation-env/bin:$PATH"
pip install -q maturin pytest
mkdir /tmp/project
for entry in Cargo.toml Cargo.lock LICENSE NOTICE README.md pyproject.toml crates python examples integrations; do
    cp -R "/source/$entry" /tmp/project/
done
cd /tmp/project
maturin build --release --manifest-path python/microloop/Cargo.toml --out /output/wheels
pip install -q /output/wheels/*.whl
python - <<'PY'
import importlib.util
import json
import platform
from pathlib import Path
from microloop.internal.model import RUNTIME_VERSION
from microloop.internal.model.registry import model_path, verify
assert importlib.util.find_spec("laya_mlx") is None
verify(model_path())
Path('/output/runtime.json').write_text(json.dumps({
    'platform': platform.platform(), 'python': platform.python_version(),
    'runtime': RUNTIME_VERSION,
}, indent=2) + '\n')
PY
python -m pytest -q python/microloop/tests --junitxml=/output/tests.xml
microloop --version
microloop sites --json
CONTAINER
