# Contributing to Microloop

Microloop is a small, deliberate project. Please keep changes focused.

By participating you agree to the [code of conduct](CODE_OF_CONDUCT.md).

## Getting started

```bash
git clone https://github.com/Devaretanmay/microloop
cd microloop
pip install -e '.[dev]'
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
crates/microloop-core   Rust runtime: detection and policy
python/microloop        PyO3 bindings, Python SDK, CLI
python/microloop/tests  Python tests and the trajectory fixture
benchmarks              evaluation harness, and perf.py for engine cost
benchmarks/schemas      authoritative run-result and event schemas
docs                    concepts, integration, CLI, architecture
examples                a runnable offline coding-agent demo
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
7. On a release, bump the `?v=` query on the PyPI badge in `README.md`. shields.io
   caches for 12 hours, so a new release otherwise keeps showing the old version
   for up to half a day.

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
