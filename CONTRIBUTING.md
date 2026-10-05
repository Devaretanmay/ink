# Contributing to Ink

Ink is a small, deliberate project. Please keep changes focused.

By participating you agree to the [code of conduct](CODE_OF_CONDUCT.md).

## Getting started

```bash
git clone https://github.com/Devaretanmay/ink
cd ink
uv venv --python 3.13
uv pip install maturin pytest ruff
maturin develop --manifest-path python/ink/Cargo.toml
make check
```

`make check` is the gate: fmt, clippy, the whole-tree lint, the Rust and Python
test suites, the documented example, and a wheel build. It is the same gate CI
runs.

Requirements: Rust MSRV 1.80 (verified in CI) and Python 3.10 to 3.13.

The benchmark harness needs its own extra:

```bash
pip install -e '.[benchmarks]'
```

Without `mini-swe-agent` installed the runner refuses to execute a real
benchmark rather than silently emitting simulated trajectories. Pass
`--dry-run`, or use the `mock` or `offline` provider, when you want the
deterministic simulation on purpose.

## Repository layout

```
crates/ink-core   Rust trajectory compatibility engine
python/ink        decision-JIT SDK, PyO3 bindings, CLI
python/ink/tests  Python tests and the trajectory fixture
benchmarks              evaluation harness, perf.py, decision_jit.py
benchmarks/schemas      authoritative run-result and event schemas
docs                    decision-JIT docs (integration, architecture, cli,
                        concepts, compatibility, validation-v0.4, laya-path,
                        implementation-status-v0.4) plus legacy/
examples                refund_agent (primary decision-JIT demo), coding-agent
                        and adaptive-coding-agent (compatibility surfaces),
                        public_calibration/
integrations            coding_harness, openai_agents, experiment runner
```

## Before opening a pull request

1. `make check` passes.
2. Behaviour changes have a test that fails without the change.
3. `ruff check .` covers the whole repository, not just `python/`.
4. Public API additions need a strong reason. Rust detection internals stay
   private or `pub(crate)`; the Python `__all__` is asserted in a test.
5. Measured claims must be reproducible. If you quote a number, add it to
   `benchmarks/perf.py` and say how to re-derive it. An unmeasured number is
   worse than no number.
6. Do not commit raw benchmark run directories, credentials, or build output.
   Published benchmark summaries go under `benchmarks/results/published/`.
7. On a release, if a PyPI badge is (re-)added to `README.md`, bump its `?v=`
   query (shields.io caches ~12h). There is currently no badge, so this is a no-op.

## Coding notes

- Detection and intervention stay separated. Detectors report evidence, the
  engine synthesizes progress state, the policy maps state to a recommendation.
- The runtime performs no I/O and executes no agent actions.
- Prefer conservative defaults: observation over intervention, unknown over
  invented evidence.
- Comments explain why. The codebase is deliberately light on them; do not add
  comments that restate the code.

## Reporting issues

Use GitHub issues. For a vulnerability, follow
[SECURITY.md](SECURITY.md) and do not open a public issue.
