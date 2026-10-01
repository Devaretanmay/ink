# Microloop Decision Model v1 — Model Card

## 1. Identity
- **Model Name:** Microloop Decision Model v1 (`microloop-decision-v1`)
- **Version:** 1.0.0
- **Status:** Microloop-trained checkpoint (`weights_modified: true`, `trained_by: microloop-finetune-v1`).
- **License:** Apache-2.0.

## 2. Base Model & Lineage
- **Base Checkpoint:** `aac6fef/laya-mlx` (revision `20aed815fc6acde75733882e7ec0e3f28aeb9717`), derived from `convaiinnovations/laya`.
- **Upstream License:** Apache-2.0 (see `NOTICE` and `upstream.json`).
- **Attribution:** The ModernBERT encoder architecture, tokenizer, and decision head structures derive from Laya / laya-mlx. Fine-tuned weights and training pipeline are owned by Microloop.

## 3. Architecture & Parameter Breakdown
- **Encoder:** ModernBERT-large (50,368 vocab, 1,024 hidden, 2,624 intermediate, 28 layers, 16 heads, RoPE attention, max context 8,192).
- **Decision Head:** 2 Transformer `HeadLayer` layers (1,024 dim, 16 heads, ReLU MLP).
- **Scorer:** LayerNorm(1024) → Linear(1024, 1024) → GELU → Linear(1024, 1).
- **Type Embedding:** Embedding(3, 1024).
- **Action Head:** Linear(1028, 256) → GELU → Linear(256, 2).
- **Parameter Counts:**
  - Total Parameters: 421,293,830 (~421.3M)
  - Trainable Parameters: 26,245,121 (6.23% — `head.*` and `scorer.*`)
  - Frozen Parameters: 395,048,709 (93.77% — `encoder.*`, `type_emb.*`, `act_head.*`)
  - Safetensors Size: 803.57 MB (float16).

## 4. Training Data & Recipe
- **Dataset:** `microloop-intent-decisions-v1` (dataset digest `193298a8d67773564550f1a170937b18c35e4880b87ab6816ffb85b9c7b31e76`).
  - Sources: Banking77 (PolyAI, CC-BY-4.0) + Microloop Refund Domain (Apache-2.0).
  - Tasks: Support Workflow Routing (3-way), Card Management (4-way), Transaction Resolution (4-way), Refund Settlement (3-way).
  - Explicit Splits: Train (750 rows), Validation (150 rows), Calibration (150 rows), Held-out Test (156 rows), OOD/Adversarial (100 rows).
- **Hyperparameters:**
  - Steps: 100
  - Batch Size: 8
  - Optimizer: Adam (learning rate 1e-4)
  - Loss: Cross-entropy over dynamic choice marker slots
  - Hardware: Apple Silicon (Metal acceleration)
  - Training duration: 113.0s.

## 5. Intended Use
- **Primary Use:** Fast-path local dispatch for bounded semantic `Choice` decisions inside Microloop Decision JIT.
- **Workflow:** Predicts choice selection + normalized confidence among declared candidate options.
- **Execution:** Synchronous or asynchronous local inference without external network calls.

## 6. Prohibited & Unsupported Uses
Do **NOT** use for:
- Unrestricted free-form reasoning or dialogue generation.
- Security authorization or authentication bypass.
- Arbitrary policy enforcement outside verified DecisionSite contracts.
- High-stakes medical, legal, or safety-critical decisions without human verification.
- Universal zero-shot classification across uncalibrated domains.

## 7. Measured Performance
- **Held-out Test Accuracy:** 62.69% (vs. Upstream 56.72%, +5.97% improvement).
- **Macro F1:** 0.5888 (vs. Upstream 0.4903, +9.85% improvement).
- **ECE (Expected Calibration Error):** 0.1573.
- **Latency (Apple Silicon M-series):**
  - p50 Latency: 47.71 ms
  - p95 Latency: 80.48 ms
  - Cold Load Time: 127.6 ms
- **Memory RSS:** ~918 MB (float16 inference).
- **Classical Baseline Comparison:** On the same dataset, TF-IDF + Naive Bayes achieves 95.52% accuracy in 0.019 ms. The neural model provides semantic flexiblity for arbitrary choice labels, but operational cost must be weighed against classical baselines.

## 8. Calibration & Temperature Recalibration
- **Upstream Flaw Fixed:** Upstream checkpoint shipped `choice:11+ = 0.1006`, which artificially multiplied logits ~10x and caused misleading high confidence.
- **Microloop Recalibration:** Recalibrated to `1.0` in `rl_agent_config.json`. Clamping ensures all temperatures remain within `[0.5, 5.0]`, preventing confidence inflation.

## 9. Limitations & Safety Boundary
- **Exact-State Qualification:** In Microloop v0.4, statistical qualification is exact-state bounded. Generalization to unseen states requires separate verification.
- **OOD Uncertainty:** Ambiguous and out-of-distribution queries produce low confidence (<0.12), triggering safe application fallback.
- **Typed Guarantee:** Neural output is constrained to declared `DecisionSite.choices`. The model cannot emit arbitrary strings or inject unauthorized commands.
- **Outcome Verifier Authority:** Neural scores are candidate proposals only. The independent replay verifier and Hoeffding confidence bounds decide promotion or demotion.
