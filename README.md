# Microloop

<div align="center">

**The ultimate circuit breaker for AI agents. Stop burning API credits on loops your agent shouldn't be running.**

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-red.svg)](LICENSE)
[![Crates.io](https://img.shields.io/crates/v/microloop)](https://crates.io/crates/microloop)
[![PyPI](https://img.shields.io/pypi/v/microloop)](https://pypi.org/project/microloop/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/microloop)](https://pypi.org/project/microloop/)
[![CI](https://github.com/Devaretanmay/microloop/actions/workflows/ci.yml/badge.svg)](https://github.com/Devaretanmay/microloop/actions)
[![Docs](https://img.shields.io/badge/docs-rs-rust?style=flat&logo=rust)](https://docs.rs/microloop)

**Rust - Python - WebAssembly - C/C++ - Go - Zero-Config Proxy**

</div>

---

## The $500 "Loop of Death"

Every AI agent developer knows this pain:
You build an autonomous agent, run it overnight, and wake up to a **$500 OpenAI bill**.

What happened? The agent tried to write a file or run a terminal command. The tool returned an error. The agent retried--with the exact same arguments. It failed again. So it retried again. It spent the next 6 hours in a tight loop, burning millions of tokens, accomplishing absolutely nothing.

**Microloop stops the bleeding. It detects and blocks agent loops in under 500 nanoseconds--before they ever leave your machine.**

---

## The Old Way vs. The Microloop Way

Traditional frameworks rely on step counters (`max_iterations = 10` or `max_tokens = 1000`). But step counters are blind.

| The Blind Counter | The Microloop Shield |
| :--- | :--- |
| **Blocks valid workflows.** If a complex 15-step plan is working perfectly, a limit of 10 kills it. | **Unlocks infinite steps.** Your agent can run 100 steps if it's making progress. |
| **Allows expensive waste.** A 2-step loop will run 5 more times before hitting a `max_iterations = 10` limit. | **Fails fast.** Blocks the loop at step 2--the very first redundant repeat. |

*Counters measure quantity. Microloop measures redundancy.*

---

## Core Pillars

### 1. Zero-Latency Fast Path
Microloop runs locally in-process. It hashes tool calls and checks for repetitive trajectories in **460 nanoseconds**. It introduces zero measurable overhead to your agent's execution.

### 2. Smart Volatile Field Masking
Naive loop detectors fail when agents include timestamps, request IDs, or random seeds in their tool arguments. Microloop automatically detects high-entropy fields (like `req_id: "9831"` changing to `req_id: "9832"`) and masks them out, catching the loop even when arguments aren't 100% identical.

### 3. Adaptive Thresholding
If a tool call returns an error, the agent is already in a high-risk state. Microloop automatically tightens its repetition thresholds on failure, forcing the agent to pivot immediately rather than hammering a broken endpoint.

### 4. Local Semantic Detection
What if the agent switches from `delete_line(5)` to `remove_line(5)` or `erase_line(5)`? Microloop's optional sidecar runs a local, ultra-fast BERT transformer model to evaluate semantic similarity--blocking loops even when tool names and syntax change.

### 5. Horizontal Redis Sync
Running a cluster of agent workers? Plug in a Redis backend to share blocklists and loop states across your entire deployment in real-time.

---

## Token Saving

Microloop compresses tool outputs before sending them to the LLM provider, saving tokens and reducing API costs. The compression pipeline detects content type and applies the optimal compressor.

| Content Type | Original Size | Compressed | Token Savings |
|---|---|---|---|
| Code Execution Result | 1032 B | 495 B | 51.9% |
| Git Diff | 1934 B | 1842 B | 7.2% |
| Log Output | 1987 B | 610 B | 70.8% |
| Search Results (JSON) | 1711 B | 1530 B | 21.9% |
| Large Tool Output | 3025 B | 517 B | 80.2% |

Total token savings across all fixtures: **50.1%**. Average compression latency: **468 microseconds** per tool message.

---

## How it Fits Into Your Architecture

Microloop can be used as a zero-code-change reverse proxy, a Python/JS middleware, or compiled directly into your native binary.

```mermaid
graph TB
    %% Nodes
    Agent[Your AI Agent / Framework]
    Proxy[Microloop Proxy <br/><i>OpenAI/Anthropic Interceptor</i>]
    Core[Microloop Core Engine <br/><i>Fast-path C/Rust/WASM SDK</i>]
    Semantic[Semantic Sidecar <br/><i>Local BERT Embeddings</i>]
    Redis[(Redis Cluster State)]
    LLM[Upstream LLM Provider <br/><i>OpenAI / Anthropic / LiteLLM</i>]

    %% Styles
    classDef main fill:#e1f5fe,stroke:#0288d1,stroke-width:2px;
    classDef agent fill:#f9f9f9,stroke:#333,stroke-width:1px;
    classDef cloud fill:#fff3e0,stroke:#f57c00,stroke-width:2px;
    classDef store fill:#eceff1,stroke:#607d8b,stroke-width:2px;

    class Proxy,Core,Semantic main;
    class Agent agent;
    class LLM cloud;
    class Redis store;

    %% Flows
    Agent -->|1. HTTP request| Proxy
    Proxy -->|2. Fast check (460ns)| Core
    Core -.->|3. Adaptive Thresholds| Core
    Proxy -.->|4. Semantic Similarity| Semantic
    Proxy -.->|5. Shared Blocklist| Redis
    Proxy -->|6. Forward if Safe| LLM
    LLM -->|7. Response| Proxy
    Proxy -->|8. Result / Auto-Intercept| Agent
```

---

## Quick Start

### Option A: The Zero-Code-Change Proxy (Recommended)
You don't need to change a single line of your agent's code. Run the Microloop proxy and point your OpenAI or Anthropic client to it.

```bash
# Start the proxy
TARGET_API_URL=https://api.openai.com OPENAI_API_KEY=sk-... cargo run -p microloop-proxy

# In your agent code, simply change the base URL:
# openai.base_url = "http://127.0.0.1:8080/v1"
```

### Option B: Python SDK
Install the official Python package backed by our high-performance Rust core.

```bash
pip install microloop
```

```python
from microloop.microloop_core import Microloop

# Initialize the engine
engine = Microloop("max_repeats: 3")

# Verify before running the tool
# Returns 0 if allowed, or a block code (1-3) if a loop is detected
result = engine.verify("write_file", '{"path": "/tmp/x.txt"}')
if result > 0:
    print("Action blocked! Forcing agent to pivot.")
```

### Option C: Rust SDK
```rust
use microloop::{MicroloopState, verify};

let mut state = MicroloopState::new("max_repeats: 3").unwrap();
let result = verify(&mut state, b"write_file", b"{\"path\": \"/tmp/x.txt\"}");
// result: 0 = Allow, 1-3 = Block (based on configured strictness)
```

---

## Ecosystem Integrations

Microloop has native integration wrappers for major AI libraries:

*   **LiteLLM**: Sub-microsecond Rust guardrails for LiteLLM proxies. [Read the LiteLLM Guide](docs/integrations/litellm.md).
*   **LangGraph**: Seamless middleware interceptors for LangGraph state machines.
*   **Model Context Protocol (MCP)**: Wrap every tool call in automatic loop detection.

---

## Performance Benchmarks

Microloop was engineered from day one for zero overhead.

| Benchmark | Microloop | Prompt-Based Guardrails |
| :--- | :--- | :--- |
| **Check Latency** | **460 nanoseconds** | ~1.5 seconds (API call) |
| **Operational Cost** | **$0.00** (Local execution) | ~$0.01 per step (Token burn) |
| **Execution Safety** | **100% Deterministic** | Probabilistic (Hallucinates/Misses) |
| **Memory Footprint** | **< 10 MB** | N/A |
| **Throughput** | **500,000+ checks/sec** | ~50 checks/sec |

---

## Configuration

A single YAML file controls the entire shield layer:

```yaml
max_repeats: 3          # Max identical calls before blocking
history_window: 8       # Rolling window size to search for loops
strictness: Balanced    # Lenient, Balanced, or Strict
tools:
  - name: execute_command
    trajectory_gate:
      volatile_fields: ["timestamp", "session_id"]
```

---

## Security & Verification

Microloop is completely safe to run in production:
*   No tool data or code ever leaves your environment.
*   Zero external API calls.
*   Apache 2.0 Licensed.

For security concerns, please refer to [SECURITY.md](SECURITY.md).
