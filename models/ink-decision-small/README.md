# Model Card: ink-decision-small

`ink-decision-small` is the default neural representation model used by the Ink compiler.

---

## Model Summary

- **Model ID:** `ink-decision-small`
- **Family:** `ink-decision`
- **Architecture:** ModernBERT-large backbone (28 layers, 1024 hidden size) with specialized decision classification heads
- **Parameters:** 421,293,830 (~421M)
- **Precision:** `float16`
- **Backend:** MLX (Apple Silicon Metal GPU & Linux CPU)
- **License:** Apache-2.0

---

## Role & Usage

### Where Ink Uses It
- **Control Plane Compilation**: Generates dense semantic representations to propose decision boundaries during background compilation.
- **Candidate Proposals**: Evaluates categorical proposals against classical lexical representations to find the most accurate compiled artifact.

### Where Ink Does NOT Use It
- **Synchronous Serving**: `ink-decision-small` **never** serves production requests directly. Live traffic is served only by qualified `Exact` or `Linear` Fast Paths, or the Host model fallback.
- **Serving Authority**: Model confidence does not grant authority. A proposal must pass independent statistical qualification before any traffic is served locally.

---

## Input & Output

- **Input**: Bounded JSON state dictionary and target choice strings.
- **Output**: Softmax choice probabilities, ambiguity margin, and task familiarity signals.

---

## Checkpoint Identity

| Component | SHA-256 Digest |
| :--- | :--- |
| `model.safetensors` | `28e7e65bfbd272c6392c52616bbb409a7a425db604df442e2682ab0155593276` |
| `manifest.json` | `cb55c8ddb04bcd61f0fd7109dbd5a65e1e93516a82586d43984686e8baf87102` |

---

## Limitations

- Trained on bounded categorical decisions. Does not generate text.
- Requires macOS (Apple Silicon) or Linux with MLX runtime dependencies.
- Effective only when paired with Ink's statistical qualification system.
