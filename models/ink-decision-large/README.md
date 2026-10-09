# Model Card: ink-decision-large

## Model Summary

- **Canonical Model ID:** `ink-decision-large`
- **Model Family:** `ink-decision`
- **Size Tier:** `large`
- **Model Version:** `1.0.0`
- **Runtime Format Version:** `2` (decoupled from model ID)
- **Checkpoint Revision:** `gliner2.5-decide-486m`
- **Architecture:** `microsoft/deberta-v3-large` (24 layers, 16 heads, hidden size 1024) + GLiNER2.5 Decide multi-label head
- **Total Parameters:** 486,444,053 (~486M)
- **Weight Precision:** `float32`
- **Execution Backend:** `gliner2` / PyTorch (MPS / CUDA / CPU)
- **Status:** Optional High-Capability Policy Model (lazy loaded, offline/teacher only)

---

## Intended Use & Serving Invariant

### Role
`ink-decision-large` is an optional high-capability neural tier within Ink. It provides:
1. **Teacher Distillation Engine:** Acts as the high-capacity teacher for local model distillation.
2. **Offline Semantic Proposal:** Enhanced classification reference for offline analysis.
3. **Ambiguity Metric:** Provides sharp probability margins on multi-class prompts.

### Core Architectural Invariant
> **"Policy models propose. Ink evidence grants authority."**

Like `ink-decision-small`, `ink-decision-large` has **ZERO serving authority by itself**. It is never loaded by default in normal Ink runtime execution and is excluded from synchronous serving paths.

---

## Upstream Lineage & Provenance

In accordance with legal and technical provenance requirements:

- **Base Architecture:** `microsoft/deberta-v3-large`
- **Upstream Checkpoint:** `fastino/GLiNER2.5-Decide`
- **Author & Publisher:** Fastino
- **License:** Apache License, Version 2.0 (see `NOTICE` and `LICENSE`)
- **Adaptation Decision:** In accordance with Model Provenance Rules, authorized upstream weights are preserved cleanly without cosmetic perturbations.

---

## Checkpoint File Integrity

All checkpoint files are pinned and cryptographically verified:

| File | SHA-256 Digest |
|---|---|
| `model.safetensors` | `40a5a23ff860dc3dff426cecd1048cacdd29c648c96db209dad818e9686dc997` |
| `config.json` | `501c6579278518e72dbba5a1f248b421dd5bfb13600db45d86c94c224368670b` |
| `special_tokens_map.json` | `84ea70143f533d7e99b393d87f20010887a9ac2cba955828ef313886e4e83f4f` |
| `tokenizer.json` | `3ad87d9ffe669147063e70850927dd2da90249e2acc5c8527f1eb65df467bcc8` |
| `tokenizer_config.json` | `323199a4e946039410899f3779f2aa3eaef1500213c512727ad0f623d4f21309` |
| `encoder_config/config.json` | `bd32f1484ba5a199f7a63df44df3814b839fffcf6e64478323c4689868ef6015` |
