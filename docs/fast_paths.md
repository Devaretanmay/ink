# Fast Paths & Execution Engines

Fast Paths are compiled, qualified local execution implementations that replace remote LLM reasoning with instant deterministic software.

---

## Fast Path Serving Architecture

Ink enforces a strictly decoupled 3-tier architecture:

```text
SERVING PATH
Exact -> Linear -> Host

LEARNING / CONTROL PLANE
Small Policy Model -> semantic proposals / regions -> Ink evidence lifecycle -> (Exact, Linear)

OFFLINE TRAINING
Large -> teacher / distillation -> Small
```

### Serving Engine Tiers (Hot Fast Path)

| Engine | Tier | Backend | Latency | Memory Footprint | Role |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`ExactEngine`** | Serving Tier 1 | Exact state hash map | $< 0.005\text{ ms}$ | $< 50\text{ KB}$ | Deterministic hash table for proven repeated states |
| **`LinearClassifierEngine`** | Serving Tier 2 | TF-IDF / regularized linear projection | $< 0.3\text{ ms}$ | $< 2\text{ MB}$ | High-throughput feature projection over qualified state boundaries |
| **`Host Model`** | Serving Tier 3 | Host application LLM / API | Variable | N/A | General fallback for novelty, ambiguity, or drift; verified outcomes remain empirical truth |

### Control Plane & Proposal Generation

| Component | Role | Latency | Hardware |
| :--- | :--- | :--- | :--- |
| **`Small Policy Model`** (`ink-decision-small`) | Asynchronous shadow proposals and frozen-encoder representations persisted as optional compiler features | ~50 ms | MLX (Apple Silicon) / CPU |

### Offline Training Tier

| Component | Role | Training Mode |
| :--- | :--- | :--- |
| **`Large Model`** (`ink-decision-large`) | Offline teacher model, knowledge distillation (KD), soft target supervision for Small | Offline / Batch only |

---

## The Qualification Gate (Hoeffding concentration bound)

Ink never promotes a candidate engine based on arbitrary training loss. Promotion requires statistical qualification using **Hoeffding's Inequality**:

Given $n$ independently verified samples with empirical mean $\hat{\mu}$, the conservative lower bound on true production quality is:

$$\text{Lower Bound} = \hat{\mu} - \sqrt{\frac{\ln(1/\alpha)}{2n}}$$

Where:
- $\alpha = 0.05$ (guaranteeing 95% confidence)
- $n = \text{number of verified holdout samples}$

A candidate is only promoted to `ACTIVE` when:
1. Holdout quality lower bound meets or exceeds `requirements.min_quality`.
2. Empirical degradation against the live model is within `requirements.max_degradation`.
3. Candidate quality is measured from independently verified outcomes; Host agreement alone grants no authority.

---

## Progressive Online Authority

For semantic workloads, Ink does not require all possible states to qualify at once. Ink organizes semantic feature space into **SemanticRegions**:
- Regions with sufficient verified evidence are individually promoted to `ACTIVE`.
- Regions still accumulating evidence remain in `SHADOW` (model continues to decide).
- Inputs that fall near boundary margins between conflicting choices are marked `ambiguous` and fall back to the host model.
