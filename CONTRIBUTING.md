# Contributing to Microloop

Welcome to Microloop. Before submitting PRs, please review:
* [Product Requirements Document (PRD)](docs/PRD.md)
* [System Architecture](docs/ARCHITECTURE.md)
* [Benchmark Specification](docs/BENCHMARK_SPEC.md)
* [Repository Scope & Boundaries](docs/REPOSITORY-SCOPE.md)

---

## 1. Engineering Principles

1. **Evidence-Based Evaluation:** Changes to detectors or policies must be validated against deterministic regression tests and the Microloop Dev Benchmark.
2. **Strict Separation of Concerns:**
   $$\text{Detector (Analysis)} \longrightarrow \text{Decision} \longrightarrow \text{Policy (Host Action)} \longrightarrow \text{Intervention}$$
   The detector must never execute tools, mutate environments, or call external models directly.
3. **Deterministic & Offline:** The core Rust monitor must remain 100% offline, local, and memory-safe, with no cloud telemetry or uncalibrated ML dependencies.
4. **Understated Precision:** Avoid hyperbolic claims in code comments or documentation. Code should be transparent, robust, and verifiable.

---

## 2. Development & Verification Workflow

Microloop requires a stable Rust toolchain supporting edition 2024 (Rust 1.85+).

```sh
# Format check
cargo fmt --all --check

# Run Rust unit and integration tests
cargo test --workspace

# Run Clippy lints
cargo clippy --workspace --all-targets -- -D warnings

# Build Python bindings
cargo build -p microloop-python

# Run basic example
cargo run --example basic
```

---

## 3. Adding Detectors or Tests

* Any new detector must include:
  1. Unit tests verifying detection on positive failure cases.
  2. Control tests verifying that healthy exploration or valid retries are **not** flagged (false positive avoidance).
  3. Bounded-history and run-isolation guarantees.
* Never tune detectors against the held-out validation manifests (`validation-v1.json`). All tuning must occur against `dev-v1.json`.
