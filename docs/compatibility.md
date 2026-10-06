# Platform Compatibility

A realistic, evidence-backed matrix of platform, engine, and hardware support for Ink.

---

## Compatibility Matrix

| Platform | Architecture | ExactEngine | DecisionModelEngine (421M) | Status | Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **macOS** | Apple Silicon (`arm64`: M1/M2/M3/M4) | ✅ Supported | ✅ Supported (Metal GPU accelerated) | **Fully Supported** | Tested in CI (`macos-latest`) and local development. Sub-millisecond exact paths; ~48 ms neural inference. |
| **macOS** | Intel (`x86_64`) | ✅ Supported | ❌ Not Supported | **Exact Only** | `mlx` does not support macOS x86_64. ExactEngine works; learned neural model requires ARM64. |
| **Linux** | x86_64 | ✅ Supported | ✅ Supported (MLX CPU backend) | **Supported** | Tested in CI (`ubuntu-latest`). Exact paths < 0.2 ms; neural inference runs on CPU via `mlx[cpu]`. |
| **Linux** | aarch64 | ✅ Supported | ✅ Supported (MLX CPU backend) | **Supported** | Same as Linux x86_64. |
| **Windows** | x86_64 / ARM | ❌ Not Supported | ❌ Not Supported | **Unsupported** | Windows is explicitly excluded from wheel CI and dependency specifications. |

---

## What We Do NOT Support

- **Windows:** Neither native nor WSL is formally supported or tested in CI.
- **CUDA / NVIDIA GPUs:** Ink uses Apple MLX as its tensor framework. On Linux, MLX runs on CPU. There is no CUDA / ROCm backend.
- **Python < 3.11:** Unsupported. Ink requires Python 3.11, 3.12, or 3.13.
- **Python ≥ 3.14:** Rejected at install time until upstream MLX and NumPy provide stable wheels.

---

## Runtime Resource Footprint

Measured on Apple Silicon (M-series, macOS 15, Python 3.13):

| Component | Metric | Measured Value | Scope |
| :--- | :--- | :--- | :--- |
| **Wheel distribution** | File size | ~90–120 KB (`ink-jit`) | Base package excluding pre-trained weights |
| **Model weights** | On-disk size | 803.57 MB (`model.safetensors`) | Pinned float16 ModernBERT-large + DecisionHead |
| **Model parameters** | Count | 421,293,830 (~421.3M) | 26.2M trainable, 395M frozen encoder |
| **Process memory (RSS)** | Idle with model loaded | ~918 MB | Baseline Python process + MLX Metal memory |
| **Process memory (RSS)** | ExactEngine only | ~45–60 MB | Standard Python + SQLite memory |
| **Cold load latency** | Model initialization | 127.6 ms | Loading safetensors into MLX memory |
| **Warm local decision latency** | ExactEngine fast path | 0.18–0.19 ms (p50) | In-process SQLite + exact table lookup |
| **Warm local decision latency** | DecisionModelEngine | 47.71 ms (p50) | Neural inference for semantic generalization |

---

## Engine Selection

Ink includes two engines out of the box:

1. **`ExactEngine`:** Deterministic, sub-millisecond dispatch (<0.2 ms) for exact canonical state matches. Zero neural inference overhead.
2. **`DecisionModelEngine`:** Pinned 421M parameter model (`ink-decision-v1`) that evaluates semantic similarity and provides candidate choice predictions for novel states.

Both engines are subject to the same strict **qualification lifecycle**: candidate predictions carry zero authority until validated against independent outcome verifiers.
