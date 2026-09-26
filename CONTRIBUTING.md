# Contributing to Microloop

Thanks for helping make agents more reliable. Microloop is a small, deliberate
project. Please keep changes focused.

## Getting started

```bash
git clone https://github.com/Devaretanmay/microloop
cd microloop
pip install -e '.[dev]'
make check
```

Requirements: stable Rust (MSRV 1.80, verified in CI) and Python 3.10–3.13.

To run the benchmark harness, install its extra as well:

```bash
pip install -e '.[benchmarks]'
```

Without `mini-swe-agent` the runner refuses to execute a "real" benchmark rather
than silently emitting simulated trajectories. Use `--dry-run`, or the `mock` /
`offline` provider, when you want the deterministic simulation.

## Repository layout

```
crates/microloop-core   Rust runtime (detection + policy)
python/microloop        PyO3 bindings, Python SDK and CLI
tests/fixtures          trajectory fixtures
benchmarks              reproducible evaluation methodology and runner
benchmarks/schemas      authoritative result and event schemas
docs                    concepts, integration, architecture
examples                a runnable coding-agent integration
```

## Before opening a PR

1. Run `make check`. It is the same gate the release workflow runs before it
   publishes anything: fmt, clippy, the whole-tree lint, the Rust and Python
   test suites, the documented example, and a wheel build.
2. Keep the public API small. New public Rust items need a strong reason;
   detection internals stay private or `pub(crate)`. The Python `__all__` is
   asserted in `test_monitor.py`.
3. Add or update a test for behaviour changes. Tests live beside the modules
   they cover and in `crates/microloop-core/tests/` for integration.
4. Lint the whole repository (`ruff check .`), not just `python/`.
5. Do not commit raw benchmark outputs; they are git-ignored. Publish only small
   summaries with explicit provenance under `benchmarks/results/published/`, and
   keep a single authoritative schema per artifact under `benchmarks/schemas/`.

## Coding notes

- Detection and intervention stay separated. Detectors report evidence; the
  engine synthesizes a progress state; the policy maps it to an intervention.
- The runtime performs no I/O and executes no agent actions.
- Prefer conservative defaults: observation over intervention, unknown over
  invented evidence.

## Reporting issues

Use GitHub issues. For security, see [SECURITY.md](SECURITY.md).
