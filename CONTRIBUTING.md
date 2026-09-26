# Contributing to Microloop

Thanks for helping make agents more reliable. Microloop is a small, deliberate
project. Please keep changes focused.

## Getting started

```bash
git clone https://github.com/Devaretanmay/microloop
cd microloop
make check
```

Requirements: stable Rust (MSRV 1.80) and Python 3.10–3.13.

## Repository layout

```
crates/microloop-core   Rust runtime (detection + policy)
python/microloop        PyO3 bindings, Python SDK and CLI
tests/fixtures          trajectory fixtures
benchmarks              reproducible evaluation methodology and runner
docs                    concepts, integration, architecture
examples                a runnable coding-agent integration
```

## Before opening a PR

1. Run `make check` (fmt, clippy, Rust tests, Python lint and tests).
2. Keep the public API small. New public types need a strong reason.
3. Add or update a test for behaviour changes. Tests live beside the modules
   they cover and in `crates/microloop-core/tests/` for integration.
4. Do not commit raw benchmark outputs; they are git-ignored. Publish only small
   summaries with explicit provenance under `benchmarks/results/published/`.

## Coding notes

- Detection and intervention stay separated. Detectors report evidence; the
  engine synthesizes a progress state; the policy maps it to an intervention.
- The runtime performs no I/O and executes no agent actions.
- Prefer conservative defaults: observation over intervention, unknown over
  invented evidence.

## Reporting issues

Use GitHub issues. For security, see [SECURITY.md](SECURITY.md).
