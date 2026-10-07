# Contributing to Ink

Ink is a small, deliberate project. Please keep changes focused.

By participating you agree to the [code of conduct](CODE_OF_CONDUCT.md).

## Getting started

```bash
git clone https://github.com/Devaretanmay/ink
cd ink
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
make check
```

`make check` is the gate: the whole-tree lint, the Python test suite, the documented
example, and a wheel build. It is the same gate CI runs.

Prefer [uv](https://docs.astral.sh/uv/) if you have it:

```bash
uv venv --python 3.13 && uv pip install -e '.[dev]'
```

Requirements: Python 3.11–3.13.

The benchmark suite needs its own extra:

```bash
pip install -e '.[benchmarks]'
```

## Repository layout

```text
python/ink        decision-JIT SDK, local model, CLI
python/ink/tests  test suite
examples/         runnable integration examples
benchmarks/       evaluation harness, schemas, and published results
docs/             user-facing documentation
scripts/          repository hygiene checks
```

## Before opening a pull request

1. `make check` passes.
2. Behaviour changes have a test that fails without the change.
3. `ruff check .` covers the whole repository.
4. Public API additions need a strong reason. The Python `__all__` is asserted in a test.
5. Measured claims must be reproducible. Add the command that re-derives the number and
   register the claim in [docs/claims.md](docs/claims.md). An unmeasured number is worse
   than no number.
6. Keep the public repo clean. Do not commit credentials, build output, generated data, or
   files that exist only for internal operations. The hygiene scripts below enforce this.

## Repository hygiene

Three checks run in CI and must pass locally:

```bash
python scripts/check-repo-hygiene.py     # no internal files, absolute local paths, or stale artifacts
python scripts/check-brand-hygiene.py    # no retired product names outside history/evidence
python scripts/check-product-hygiene.py  # no retired positioning or wrong install command
```

`check-repo-hygiene.py` rejects tracked internal files, `__pycache__`/`.pyc`, wheels and
databases, and any hard-coded absolute local path (a machine-specific home directory or
user path). If it fails, remove the file or make the content machine-independent — do not
add it to an allowlist unless it is a documented exception.

## Coding notes

- Candidate generation and serving authority stay separated. A model prediction carries no
  serving authority on its own.
- Prefer conservative defaults: observation over serving, unknown over invented evidence.
- The runtime performs no I/O in the serving path and executes no agent actions.
- Comments explain why. The codebase is deliberately light on them; do not restate the code.

## Reporting issues

Use GitHub issues. For a vulnerability, follow [SECURITY.md](SECURITY.md) and do not open a
public issue.
