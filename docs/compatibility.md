# Platform Compatibility

A realistic, evidence-backed matrix of platform, engine, and hardware support for Ink.

---

## Compatibility Matrix

| Platform | Architecture | ExactEngine | ink-decision-small (421M) | ink-decision-large (486M) | Status | Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **macOS** | Apple Silicon (`arm64`: M1–M4) | ✅ Supported | ✅ Supported (MLX Metal GPU) | ✅ Supported (PyTorch MPS/CPU) | **Fully Supported** | Sub-millisecond exact paths; ~47 ms small neural inference. |
| **macOS** | Intel (`x86_64`) | ✅ Supported | ❌ Not Supported (`mlx`) | ✅ Supported (PyTorch CPU) | **Exact / Large CPU** | `mlx` lacks macOS x86_64; large tier runs on PyTorch CPU. |
| **Linux** | x86_64 | ✅ Supported | ✅ Supported (MLX CPU) | ✅ Supported (PyTorch CUDA/CPU) | **Supported** | Small runs via `mlx[cpu]`; large tier runs on CPU or NVIDIA CUDA. |
| **Linux** | aarch64 | ✅ Supported | ✅ Supported (MLX CPU) | ✅ Supported (PyTorch CPU/CUDA) | **Supported** | Same as Linux x86_64. |
| **Windows** | x86_64 / ARM | ❌ Not Supported | ❌ Not Supported | ❌ Not Supported | **Unsupported** | Windows explicitly excluded from dependency specifications. |

---

## What We Do NOT Support

- **Windows:** Neither native nor WSL is formally supported or tested in CI.
- **CUDA for ink-decision-small:** Small model uses Apple MLX as tensor backend. Large model supports PyTorch CUDA.
- **Python < 3.11:** Unsupported. Ink requires Python 3.11, 3.12, or 3.13.
- **Python ≥ 3.14:** Rejected at install time until upstream MLX and NumPy provide stable wheels.

---

## Runtime Resource Footprint

Measured on Apple Silicon (M-series, macOS 15, Python 3.13):

| Component | Metric | Measured Value | Scope |
| :--- | :--- | :--- | :--- |
| **Wheel distribution** | File size | ~90–120 KB (`ink-jit`) | Base package excluding pre-trained weights |
| **ink-decision-small** | On-disk size / params | 803.57 MB / 421,293,830 | Pinned float16 ModernBERT-large + DecisionHead (MLX) |
| **ink-decision-large** | On-disk size / params | ~1.9 GB / 486,444,053 | Pinned float32 DeBERTa-v3-large + GLiNER2 multi-label (PyTorch) |
| **Warm decision latency** | ExactEngine fast path | 0.18–0.19 ms (p50) | In-process SQLite + exact table lookup |
| **Warm decision latency** | ink-decision-small | 47.71 ms (p50) | Neural inference for local proposal and scoring |

---

## Policy Model Selection

Ink provides a canonical model configuration:

```python
loop = Ink(policy_model="small")  # Default: ink-decision-small
loop = Ink(policy_model="large")  # Optional: ink-decision-large
loop = Ink(policy_model="auto")   # Ink selection policy
loop = Ink(policy_model=None)     # Neural proposal disabled
```

1. **`ExactEngine`:** Deterministic, sub-millisecond dispatch (<0.2 ms) for exact canonical state matches. Zero neural inference overhead.
2. **`PolicyModelEngine` (`ink-decision-small`):** Pinned 421M parameter model providing local proposals, ambiguity signals, and familiarity estimates.
3. **`PolicyModelEngine` (`ink-decision-large`):** Optional 486M parameter model providing high-capacity proposals for difficult cold starts.

Legacy `ink-decision-v1` resolves with a one-time deprecation warning to `ink-decision-small`. All policy proposals carry **zero serving authority** until validated by empirical outcome evidence.
