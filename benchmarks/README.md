# Microloop Benchmarks

This directory contains the reproducible benchmark suite evaluating the Microloop Decision JIT across live cloud LLMs, concentration bounds, long-horizon economics, and multi-step agent workloads.

---

## Benchmark Suite Overview

| Benchmark Script | Focus & Methodology | Primary Result File |
| :--- | :--- | :--- |
| [`benchmark_real_world.py`](benchmark_real_world.py) | Live cloud LLM validation (Groq `qwen/qwen3.8-27b`) with prompt parity across 2,100 decisions (support routing, tool selection, incident escalation). | `results/real_world_validation_v2.json` |
| [`benchmark_qualification_efficiency.py`](benchmark_qualification_efficiency.py) | Concentration bound study comparing Hoeffding vs Empirical Bernstein vs Howard et al. sequential bounds. | `results/qualification_efficiency.json` |
| [`benchmark_long_horizon.py`](benchmark_long_horizon.py) | Long-horizon cumulative simulation across 10k, 100k, and 1,000,000 decisions testing the 75–85% steady-state claim. | `results/long_horizon_economics.json` |
| [`benchmark_agent_site_selection.py`](benchmark_agent_site_selection.py) | Multi-step agent site profiling (`agent.intent`, `agent.tool`, `agent.cont`) demonstrating selective compilation economics. | `results/agent_site_selection.json` |
| [`validate_semantic_lifecycle.py`](validate_semantic_lifecycle.py) | Sparse TF-IDF semantic coverage recall, negative margin bounds, and counterexample contraction. | Test evidence |

---

## Running Benchmarks

### 1. Qualification Sample Efficiency
Evaluates sample efficiency across clear winner, borderline, substandard, and adversarial distributions:

```bash
PYTHONPATH=python/microloop python benchmarks/benchmark_qualification_efficiency.py
```

### 2. Long-Horizon Economics Simulation
Simulates cumulative model-call reduction, dollar savings, and latency savings over 10k, 100k, and 1M decisions under Zipfian and uniform distributions:

```bash
PYTHONPATH=python/microloop python benchmarks/benchmark_long_horizon.py
```

### 3. Agent Site Selection & Selective Compilation
Evaluates selective compilation vs blind compile-all strategies on multi-step agent traces:

```bash
PYTHONPATH=python/microloop python benchmarks/benchmark_agent_site_selection.py
```

### 4. Real-World Live Cloud LLM Validation (Requires `GROQ_API_KEY`)
Executes 2,100 decisions against live cloud LLM inference, measuring latency, cost, and false-serve rates during policy drift:

```bash
export GROQ_API_KEY="your-api-key"
PYTHONPATH=python/microloop python benchmarks/benchmark_real_world.py
```

---

## Empirical Benchmark Highlights

- **Latency Reduction:** From 126.6 ms (remote LLM p50) to **0.409 ms** (Microloop p50) on support ticket routing.
- **Drift Protection:** **99.6% reduction in false serves** compared to naive semantic caching (4 false serves vs 1,026 false serves across 2,100 decisions).
- **Steady-State Avoidance:** **80.67% avoided** at 100k decisions and **91.32% avoided** at 1M decisions under Zipfian ($s=1.1$) traffic with drift interval $\ge 20,000$.
- **Selective Compilation ROI:** Compiling only recommended high-repetition sites produces higher net dollar ROI than compiling all sites blindly.

All claims derived from these benchmarks are formally registered in [`docs/claims.md`](../docs/claims.md).
