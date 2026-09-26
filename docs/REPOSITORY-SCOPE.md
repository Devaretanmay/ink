# Repository Scope & Boundaries: Version 0.3 / Experiment 001

## 1. Architectural Scope & Focus

Microloop has narrowed its primary engineering focus to **Agent Reliability and Trajectory Validation**.

| Subsystem | Scope Decision | Rationale |
|---|---|---|
| **Rust Trajectory Monitor (`src/monitor.rs`)** | **Core Primitive** | Implements the 8 non-progress detectors and evidence collector in memory-safe, zero-overhead Rust. |
| **Policy State Machine (`src/policy.rs`)** | **Core Primitive** | Decouples detection from host intervention (Observe, Replan, Stop). |
| **Python SDK (`python/microloop-python`)** | **Primary SDK** | High-performance PyO3 binding enabling direct integration with Python agent loops (SWE-bench, LangGraph, custom). |
| **Validation Benchmark Suite (`benchmarks/`)** | **Active Priority** | Complete benchmark harness (SWE-bench Verified, Terminal-Bench, 50 fault scenarios, baseline runners). |
| **Context Compression / CCR (`crates/microloop-compress`)** | **Retained (Excluded from default build)** | Retained as the underlying engine for long-horizon Checkpoint Compaction in v0.4. Excluded from v0.3 benchmarks to eliminate experimental confounders. |
| **Legacy `Microloop.verify` API (`src/state.rs`)** | **Deprecated / Maintained for Compatibility** | Simple tool-call repeat counter. Preserved for backward compatibility, but marked as non-progress detector. |
| **Reverse Proxy (`crates/microloop-proxy`)** | **Pruned** | Premature network infrastructure layer. Proxies obscure trajectory observability and add latency. |
| **WASM / C++ / Go Bindings** | **Pruned / Deferred** | Removed to focus 100% of engineering bandwidth on Rust engine + Python benchmark harness. |
| **External Agent Framework Wrappers** | **Pruned / Inverted** | Rather than maintaining 10 brittle wrappers (LangChain, CrewAI, AutoGen), Microloop exposes an adapter-friendly canonical Event API. |

---

## 2. Hard Invariants

1. **No External Network Dependencies:** The core monitor compiles without network sockets, telemetry endpoints, or cloud dependencies.
2. **No Prompts Sent to Cloud:** State fingerprints, errors, and actions are evaluated locally.
3. **No Uncalibrated Marketing Metrics:** Claims such as "100% deterministic safety", "zero measurable overhead", or "$500 loop of death" are retired. Microloop measures and reports empirical metrics: Autonomous Completion Rate (ACR), Recovery Rate, and Damaging Intervention Rate.
4. **Deterministic Testing:** All detectors and state transitions must pass deterministic regression suites before being used in benchmark runs.
