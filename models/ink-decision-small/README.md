# Model Card: ink-decision-small

## Model Summary

- **Canonical Model ID:** `ink-decision-small`
- **Model Family:** `ink-decision`
- **Size Tier:** `small`
- **Model Version:** `1.0.0`
- **Runtime Format Version:** `2` (decoupled from model ID)
- **Checkpoint Revision:** `phase18-full-kd`
- **Architecture:** ModernBERT-large (28 layers, 16 heads, hidden size 1024) + distilled DecisionHead (2 layers) + Scorer (2 layers) + act_head
- **Total Parameters:** 421,293,830 (~421M)
- **Weight Precision:** `float16`
- **Execution Backend:** MLX (optimized Apple Silicon Metal GPU / Linux CPU)
- **Status:** Default Neural Policy Model for Ink

---

## Intended Use & Serving Invariant

### Role
`ink-decision-small` acts as an out-of-band semantic proposal and representation engine within Ink's control plane. It provides:
1. **Local Semantic Proposal:** Rapid categorical choice proposals on structured and semi-structured states.
2. **Confidence Estimation:** Calibrated probabilities per choice.
3. **Ambiguity Signal:** Margin metrics between top competing candidates.
4. **Familiarity / In-Distribution Signal:** Learned task-space familiarity bounds via `act_head`.
5. **Semantic Feature Representation:** Dense representations for compilation candidates.

### Core Architectural Invariant
> **"Policy models propose. Ink evidence grants authority."**

`ink-decision-small` has **ZERO serving authority by itself**. It cannot serve customer traffic directly without rigorous compilation, empirical qualification, statistical verification bounds, and runtime drift guards managed by the Ink runtime engine.

---

## Lineage & Provenance

1. **Base Encoder:** ModernBERT-large (`answerdotai/ModernBERT-large`)
2. **Canonical Lineage:** ModernBERT-large $\to$ `ink-decision-v1` $\to$ Phase 18 Stage 1 $\to$ Phase 18 Stage 2 $\to$ Phase 18 Full KD $\to$ `ink-decision-small 1.0.0`
3. **Adaptation Pass:** Phase 18 Full Knowledge Distillation (top-2 encoder unfreeze, 600 steps)
4. **Distillation Teacher:** `ink-decision-large` (`fastino/GLiNER2.5-Decide`, 486M DeBERTa-v3-large, Apache-2.0)
5. **Training Objective:**
   $$\mathcal{L} = 0.7 \times \mathcal{L}_{\text{CE}}(\text{verified}) + 0.3 \times \tau^2 \times \mathcal{L}_{\text{KL}}(\text{teacher}_\tau \parallel \text{student}_\tau)$$
   - Temperature schedule: $\tau = 4$ (steps 1–300) $\to$ $\tau = 2$ (steps 301–600)
   - Teacher disagreement: KL contribution $\times 0.5$ on disagreement rows
   - Trainable parameters: DecisionHead + top-2 encoder layers (layers 26–27)
   - Optimization: batch 16, lr 5e-5, AdamW, cosine schedule
6. **Training Data:** 5,598 verified multi-domain decision rows, final loss: 0.2474.

---

## Checkpoint File Integrity

All checkpoint files are pinned and cryptographically verified:

| File | SHA-256 Digest |
|---|---|
| `model.safetensors` | `28e7e65bfbd272c6392c52616bbb409a7a425db604df442e2682ab0155593276` |
| `rl_agent_config.json` | `f1ee15bdcc840e91f649e6eefe2c7dc3d1c7779cecc66215a01fccc10c02de08` |
| `encoder/config.json` | `bf3ab80598fdccf414855a2ce80f22859e4492d06ca8a62ddd1cfb63972f8979` |
| `tokenizer/tokenizer.json` | `6c8aaa9a542084f2457eab775d4eeb51f92a70c0fd9de28d5edb0ddec3c08d30` |
| `tokenizer/tokenizer_config.json` | `50044de60daaa73df97d262e15a40d4faf0160e7d742df64b377877a1320dd12` |

---

## Backwards Compatibility

- **Legacy Aliases:** `ink-decision-v1`, `decision-v1`, `small`
- Access via legacy aliases triggers a single `DeprecationWarning` per process.
- Stored checkpoints at `~/.cache/ink/models/decision-v1` are migrated atomically via hardlinks to `~/.cache/ink/models/ink-decision-small/` without duplicating disk storage.
