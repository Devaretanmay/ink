# Model Card: ink-decision-large

`ink-decision-large` is an offline teacher model used for distillation research.

---

## Model Summary

- **Model ID:** `ink-decision-large`
- **Family:** `ink-decision`
- **Architecture:** `microsoft/deberta-v3-large` backbone (24 layers, 1024 hidden size) with multi-label classification head
- **Parameters:** 486,444,053 (~486M)
- **Precision:** `float32`
- **Backend:** `gliner2` & PyTorch (CPU / CUDA / MPS)
- **License:** Apache-2.0

---

## Role & Status

### Offline-Only Status
`ink-decision-large` is **never loaded** during normal production runtime or serving paths. It is an optional, offline teacher model.

### Teacher Relationship
- Acts as a high-capacity teacher to distill knowledge into `ink-decision-small`.
- Used in offline experiments to analyze complex semantic distributions.
- Has zero serving authority in the runtime engine.

---

## Dependencies

Using `ink-decision-large` requires optional dependencies:

```bash
pip install 'ink-jit[large]'
# Or: pip install gliner2 torch transformers
```

---

## Checkpoint Identity

| Component | SHA-256 Digest |
| :--- | :--- |
| `model.safetensors` | `40a5a23ff860dc3dff426cecd1048cacdd29c648c96db209dad818e9686dc997` |
| `manifest.json` | `017cba5dc2463bb1b59005423854b7324fa7522fe9d5ba0c9b7fae69e46a9a7a` |

---

## Upstream Lineage & Provenance

- **Base Architecture:** `microsoft/deberta-v3-large`
- **Upstream Checkpoint:** `fastino/GLiNER2.5-Decide`
- **Publisher:** Fastino
- **License:** Apache License, Version 2.0
