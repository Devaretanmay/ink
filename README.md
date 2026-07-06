# Microloop

**The circuit breaker and context compressor for AI agents.**

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Crates.io](https://img.shields.io/crates/v/microloop)](https://crates.io/crates/microloop)
[![PyPI](https://img.shields.io/pypi/v/microloop)](https://pypi.org/project/microloop/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/microloop)](https://pypi.org/project/microloop/)
[![CI](https://github.com/Devaretanmay/microloop/actions/workflows/ci.yml/badge.svg)](https://github.com/Devaretanmay/microloop/actions)

**Rust · Python · WebAssembly · C/C++ · Go · Zero-Config Proxy**

---

## The $500 Loop of Death

You build an autonomous agent, run it overnight, and wake up to a $500 OpenAI bill. 

The agent tried to write a file or run a terminal command. The tool returned an error. The agent retried with the exact same arguments. It failed again. It spent the next six hours in a tight loop, burning millions of tokens and accomplishing nothing. 

Worse, the tool outputs 50,000 tokens of raw JSON. The LLM gets buried in the noise, loses its reasoning thread, and loops harder.

Microloop stops the bleeding. It intercepts agent tool calls and LLM context. It blocks repetitive loops in 460 nanoseconds and compresses bloated tool outputs by up to 95% before they ever reach the model.

---

## Blind Counters vs. Microloop

Traditional frameworks rely on step counters (`max_iterations = 10`). Step counters are blind to the actual execution state.

| The Blind Counter | The Microloop Shield |
| :--- | :--- |
| **Blocks valid workflows.** If a complex 15-step plan is working perfectly, a limit of 10 kills it. | **Unlocks infinite steps.** Your agent can run 100 steps if it is making unique progress. |
| **Allows expensive waste.** A 2-step loop will run 8 more times before hitting a limit of 10. | **Fails fast.** Blocks the loop at step 3—the very first redundant repeat. |
| **Ignores context bloat.** Passes 100k tokens of raw tool output to the LLM, causing confusion. | **Compresses context.** Reduces tool outputs by 60-95% while keeping the original data retrievable. |

Counters measure quantity. Microloop measures redundancy and information density.

---

## Core Architecture

### 1. Sub-Microsecond Fast Path
Microloop runs locally in-process. The pure Rust core hashes tool calls and checks the ring buffer for repetitive trajectories in 460 nanoseconds. It introduces zero measurable overhead to your agent's execution.

### 2. Context Compression & CCR
Microloop intercepts `role: tool` outputs and routes them through specialized compression engines (JSON arrays, AST-based code, build logs, and prose). It reduces token count by 60-95%. 
Original payloads are stored in a local SQLite Cache-Compress-Retrieve (CCR) store. If the LLM realizes it needs the dropped data, it calls a retrieval tool. The compression is lossy on the wire, but lossless end-to-end.

### 3. Amortized Trajectory Injection
When a loop is blocked, Microloop does not just return a generic error. The proxy maintains a rolling, highly compressed summary of the agent's recent actions. Upon blocking, it injects this dense trajectory summary directly into the rejection message. This forces the LLM to see exactly why it is stuck and pivot, with zero synchronous latency added to the rejection path.

### 4. Smart Volatile Field Masking
Naive loop detectors fail when agents include timestamps, request IDs, or random seeds in their tool arguments. Microloop automatically detects high-entropy fields (like `req_id: "9831"` changing to `req_id: "9832"`) and masks them out, catching the loop even when arguments are not 100% identical.

### 5. Adaptive Thresholding
If a tool call returns an error, the agent is already in a high-risk state. Microloop automatically tightens its repetition thresholds on failure, forcing the agent to pivot immediately rather than hammering a broken endpoint.

### 6. Local Semantic Detection
If the agent switches from `delete_line(5)` to `remove_line(5)`, deterministic hashing misses it. Microloop's optional sidecar runs a local, ultra-fast BERT transformer model to evaluate semantic similarity, blocking loops even when tool names and syntax change.

### 7. Horizontal Redis Sync
Running a cluster of agent workers? Plug in a Redis backend to share blocklists and loop states across your entire deployment in real-time.

---

## System Flow

Microloop can be used as a zero-code-change reverse proxy, a Python/JS middleware, or compiled directly into your native binary.

```mermaid
graph TB
    %% Nodes
    Agent[Your AI Agent]
    Proxy[Microloop Proxy]
    Core[Core Engine <br/><i>Stateless Hashing</i>]
    Compressor[Compression Engine <br/><i>JSON / Code / Logs</i>]
    CCR[(Local SQLite CCR)]
    Semantic[Semantic Sidecar <br/><i>Local BERT</i>]
    Redis[(Redis Cluster)]
    LLM[Upstream LLM Provider]

    %% Styles
    classDef main fill:#f8f9fa,stroke:#343a40,stroke-width:2px;
    classDef agent fill:#e9ecef,stroke:#495057,stroke-width:1px;
    classDef cloud fill:#fff,stroke:#adb5bd,stroke-width:2px;
    classDef store fill:#f1f3f5,stroke:#868e96,stroke-width:2px;

    class Proxy,Core,Compressor main;
    class Agent agent;
    class LLM cloud;
    class CCR,Redis,Semantic store;

    %% Flows
    Agent -->|1. HTTP Request| Proxy
    Proxy -->|2. Fast Check 460ns| Core
    Proxy -->|3. Compress Tool Outputs| Compressor
    Compressor -->|4. Cache Originals| CCR
    Proxy -.->|5. Semantic Check| Semantic
    Proxy -.->|6. Shared State| Redis
    Proxy -->|7. Forward Compressed Context| LLM
    LLM -->|8. Response| Proxy
    Proxy -->|9. Result / Block Injection| Agent
```

---

## Quick Start

### Option A: The Zero-Code-Change Proxy
Run the Microloop proxy and point your OpenAI or Anthropic client to it. No changes to your agent code required.

```bash
# Start the proxy
TARGET_API_URL=https://api.openai.com OPENAI_API_KEY=sk-... cargo run -p microloop-proxy

# In your agent code, simply change the base URL:
# openai.base_url = "http://127.0.0.1:8080/v1"
```

### Option B: Python SDK
Install the official Python package backed by the Rust core.

```bash
pip install microloop
```

```python
from microloop import Microloop

# Initialize the engine
engine = Microloop("max_repeats: 3")

# Verify before running the tool
# Returns 0 if allowed, or a block code if a loop is detected
result = engine.verify("write_file", '{"path": "/tmp/x.txt"}')

if result > 0:
    print("Action blocked. Trajectory summary injected.")

# If the LLM needs the original uncompressed tool output:
original_data = engine.retrieve("sha256_hash_of_original_payload")
```

### Option C: Rust SDK
```rust
use microloop::{MicroloopState, verify};

let mut state = MicroloopState::new("max_repeats: 3").unwrap();
let result = verify(&mut state, b"write_file", b"{\"path\": \"/tmp/x.txt\"}");
// result: 0 = Allow, >0 = Block
```

---

## Ecosystem Integrations

Microloop has native integration wrappers for major AI libraries:

*   **LiteLLM**: Sub-microsecond Rust guardrails for LiteLLM proxies.
*   **LangGraph**: Middleware interceptors for LangGraph state machines.
*   **Model Context Protocol (MCP)**: Wrap every tool call in automatic loop detection and compression.

---

## Performance Benchmarks

Microloop is engineered for zero overhead. 

| Metric | Microloop | Prompt-Based Guardrails |
| :--- | :--- | :--- |
| **Check Latency** | **460 nanoseconds** | ~1.5 seconds (API call) |
| **Context Reduction** | **60% - 95%** | 0% (Passes raw output) |
| **Operational Cost** | **$0.00** (Local execution) | ~$0.01 per step (Token burn) |
| **Execution Safety** | **100% Deterministic** | Probabilistic (Hallucinates/Misses) |
| **Memory Footprint** | **< 10 MB** | N/A |
| **Throughput** | **500,000+ checks/sec** | ~50 checks/sec |

---

## Configuration

A single YAML file controls the shield layer and compression parameters:

```yaml
# Loop Detection
max_repeats: 3          # Max identical calls before blocking
history_window: 8       # Rolling window size to search for loops
strictness: Balanced    # Lenient, Balanced, or Strict

# Context Compression
compression:
  enabled: true         # Turn on tool output compression
  ccr_enabled: true     # Cache originals for lossless retrieval
  target_ratio: 0.8     # Target 80% reduction in token count

# Tool Specific Overrides
tools:
  - name: execute_command
    trajectory_gate:
      volatile_fields: ["timestamp", "session_id"]
```

---

## Security & Verification

Microloop is built for production environments. 
*   No tool data, code, or context ever leaves your environment.
*   Zero external API calls for loop detection or compression.
*   The semantic sidecar runs entirely on local ONNX models.
*   Apache 2.0 Licensed.

For security concerns or to report a vulnerability, please refer to [SECURITY.md](SECURITY.md).
