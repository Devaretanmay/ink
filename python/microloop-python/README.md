# 🔁 Microloop for Python

<div align="center">

**Stop burning API credits on loops your AI agent shouldn't be running. High-performance, Rust-backed agent loop protection for Python.**

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-red.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/microloop)](https://pypi.org/project/microloop/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/microloop)](https://pypi.org/project/microloop/)

</div>

---

##  The $500 "Loop of Death"

Every AI agent developer knows this pain:
You build an autonomous agent, run it overnight, and wake up to a **$500 OpenAI bill**. 

What happened? The agent tried to write a file or run a terminal command. The tool returned an error. The agent retried—with the exact same arguments. It failed again. So it retried again. It spent the next 6 hours in a tight loop, burning millions of tokens, accomplishing absolutely nothing.

**Microloop stops the bleeding. It detects and blocks agent loops in under 500 nanoseconds—before they ever leave your machine.**

---

## ⚡ The Old Way vs. The Microloop Way

Traditional frameworks rely on step counters (`max_iterations = 10` or `max_tokens = 1000`). But step counters are blind.

| The Blind Counter | The Microloop Shield |
| :--- | :--- |
| **Blocks valid workflows.** If a complex 15-step plan is working perfectly, a limit of 10 kills it. | **Unlocks infinite steps.** Your agent can run 100 steps if it's making progress. |
| **Allows expensive waste.** A 2-step loop will run 5 more times before hitting a `max_iterations = 10` limit. | **Fails fast.** Blocks the loop at step 2—the very first redundant repeat. |

*Counters measure quantity. Microloop measures redundancy.*

---

##  Core Pillars

### 1. Zero-Latency Fast Path
Microloop runs locally in-process. It hashes tool calls and checks for repetitive trajectories in **460 nanoseconds** using our native Rust core module under the hood.

### 2. Smart Volatile Field Masking
Naive loop detectors fail when agents include timestamps, request IDs, or random seeds in their tool arguments. Microloop automatically detects high-entropy fields (like `req_id: "9831"` changing to `req_id: "9832"`) and masks them out, catching the loop even when arguments aren't 100% identical.

### 3. Adaptive Thresholding
If a tool call returns an error, the agent is already in a high-risk state. Microloop automatically tightens its repetition thresholds on failure, forcing the agent to pivot immediately rather than hammering a broken endpoint.

### 4. Local Semantic Detection
What if the agent switches from `delete_line(5)` to `remove_line(5)` or `erase_line(5)`? Microloop's optional sidecar runs a local, ultra-fast BERT transformer model to evaluate semantic similarity—blocking loops even when tool names and syntax change.

### 5. Horizontal Redis Sync
Running a cluster of agent workers? Plug in a Redis backend to share blocklists and loop states across your entire deployment in real-time.

---

## 🔌 Quick Start

### Installation
Install the official Python package backed by our high-performance Rust core:

```bash
pip install microloop
```

### Basic SDK Usage

```python
from microloop.microloop_core import Microloop

# Initialize the engine with YAML config rules
engine = Microloop("max_repeats: 3")

# Verify before running the tool
# Returns 0 if allowed, or a block code (1-3) if a loop is detected
result = engine.verify("write_file", '{"path": "/tmp/x.txt"}')
if result > 0:
    print("Action blocked! Forcing agent to pivot.")
```

### LiteLLM Integration
If you are using LiteLLM to interface with LLMs:

```python
import litellm
from microloop.integrations import MicroloopLiteLLMGuardrail

# Plug in the guardrail callback
guardrail = MicroloopLiteLLMGuardrail(max_repeats=3)
litellm.callbacks = [guardrail]

# Every session/request is automatically tracked
```

---

## 📊 Performance Benchmarks

Microloop was engineered from day one for zero overhead.

| Benchmark | Microloop | Prompt-Based Guardrails |
| :--- | :--- | :--- |
| **Check Latency** | **460 nanoseconds** | ~1.5 seconds (API call) |
| **Operational Cost** | **$0.00** (Local execution) | ~$0.01 per step (Token burn) |
| **Execution Safety** | **100% Deterministic** | Probabilistic (Hallucinates/Misses) |
| **Memory Footprint** | **< 10 MB** | N/A |
| **Throughput** | **500,000+ checks/sec** | ~50 checks/sec |

---

## ⚙️ Configuration

A single YAML string controls the entire shield layer:

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

## 🔒 Security & Verification

Microloop is completely safe to run in production:
*   No tool data or code ever leaves your environment.
*   Zero external API calls.
*   Apache 2.0 Licensed.
