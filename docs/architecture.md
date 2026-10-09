# Ink Architecture

Ink is a **behavior JIT for production AI**. It intercepts repetitive, bounded agent decisions and executes them locally in sub-milliseconds with mathematical safety guarantees, falling back seamlessly to the host application's original model whenever novelty, uncertainty, or policy drift occurs.

```text
SERVING PATH

Exact
  ↓
Linear
  ↓
Host


LEARNING / CONTROL PLANE

         Small Policy Model
                │
     semantic proposals / regions
                │
                ▼
       Ink evidence lifecycle
          /              \
       Exact            Linear


OFFLINE TRAINING

Large
  ↓
teacher / distillation
  ↓
Small
```

---

## 1. The Three Cardinal Planes

The architecture strictly decouples three operational planes:

1. **Serving Path (Hot Fast Path):**
   ```text
   Exact -> Linear -> Host
   ```
   At runtime inference, serving happens strictly across lightweight, deterministic, and bounded linear engines:
   - **`Exact` (`ExactEngine`):** $O(1)$ hash table lookup (0.002 ms, 100% verified accuracy on identical repeated states).
   - **`Linear` (`LinearClassifierEngine`):** $O(D)$ compact linear projection (0.25 ms, regularized feature projection over qualified state boundaries).
   - **`Host` (Host Model Fallback):** The host application's original model / LLM path, executed whenever local artifacts are unverified, novel, or drifted. It is a fallback, not ground truth.
   - **Verified outcomes:** Empirical truth signals used by Ink's evidence lifecycle to grant or revoke serving authority.
   *Heavy neural policy models do NOT sit on the hot inference path for unverified requests.*

2. **Learning / Control Plane:**
   ```text
            Small Policy Model
                   │
        semantic proposals / regions
                   │
                   ▼
          Ink evidence lifecycle
             /              \
          Exact            Linear
   ```
   The **Small Policy Model** (`ink-decision-small`, ~421M ModernBERT-large + distilled DecisionHead, ~50 ms) operates in the control plane:
   - Evaluates observations in shadow/background mode.
   - Extracts semantic embeddings, proposes decision candidates, and discovers semantic prototype regions and negative margins.
   - Feeds candidate evidence into the **Ink evidence lifecycle** (`OBSERVE -> SHADOW -> CANARY -> ACTIVE`).
   - Compiles verified outcomes into **`Exact`** hash entries and **`Linear`** classifier weights.

3. **Offline Training & Distillation:**
   ```text
   Large -> teacher / distillation -> Small
   ```
   The **Large Model** (`ink-decision-large`, ~486M DeBERTa-v3-large) operates exclusively offline:
   - High-capability teacher model for knowledge distillation (KD) and silver target generation.
   - Generates soft-target distributions to train and calibrate `Small`.
   - Never deployed on the hot serving path.

```text
candidate != authority
```

---

## 2. Architectural Invariants

### Invariant 1 — Ink owns a learned policy model family
The Decision Engine includes canonical Ink-owned learned policy models (`ink-decision-small` and `ink-decision-large`) trained and distilled specifically for bounded categorical decisions. Policy models propose hypotheses; Ink qualification evidence decides authority.

### Invariant 2 — Serving authority is separate from candidate production
A candidate emitted by an internal learned model or exact engine has **zero serving authority** on its own. Serving authority requires:
1. An active, integrity-verified artifact.
2. The incoming state strictly falling within qualified coverage boundaries.
3. Model confidence meeting or exceeding site promotion requirements.
4. Passing non-comparison traffic checks.
If any condition is not met, the host application's original fallback model executes.

### Invariant 3 — Canonical Policy Models: Small (default) and Large (optional)
`ink-decision-small` (~421M parameters) ships as the default neural policy model for Ink, running with optimized sub-50ms execution on Apple Silicon Metal and Linux. `ink-decision-large` (~486M parameters) is an optional high-capability tier for difficult cold starts and complex semantic boundaries. Configuration is controlled canonically via `policy_model="small"|"large"|"auto"|None` or `INK_POLICY_MODEL`. Legacy `ink-decision-v1` survives strictly as a deprecated alias pointing to `ink-decision-small`.

### Invariant 4 — Exact fast paths are an execution tier, not a separate product
`ExactEngine` is an ultra-fast qualified execution tier within the Decision Engine. It operates analogously to a compiler branch optimization: when repeated exact states accumulate conclusive statistical evidence, Ink serves them directly in under 0.2ms, bypassing full model inference.

### Invariant 5 — Sparse / semantic mechanisms govern coverage and boundaries
Sparse n-gram vectorization and spherical semantic regions define representation boundaries. They determine whether an incoming state resembles verified behavior, calculate distance margins, and trigger abstention when an input falls outside safe regions.

### Invariant 6 — Original host model fallback is always retained
Ink handles proven, repetitive behavior; the host model handles novelty, exploration, and ambiguity. Novel, uncertain, unsupported, or drifted requests always execute through the host model fallback callable.

---

## 3. Decision Dispatch Flow

```text
Incoming State
     │
     ▼
Validate Contract Schema ──(invalid)──▶ Fail-open to Host Fallback
     │
     ▼
Check Kill Switches ──────(disabled)──▶ Fail-open to Host Fallback
     │
     ▼
Lookup Active Artifact ───(missing)───▶ Observe Mode (Host Fallback)
     │
     ▼
Evaluate Coverage Region
  ├── Exact State Match ───────────────▶ Candidate from Exact Tier
  ├── Within Qualified Semantic Region ─▶ Candidate from Learned Engine
  └── Outside Known Coverage ──────────▶ Host Fallback (reason="outside_coverage")
     │
     ▼
Verify Authority Conditions
  ├── Artifact Status == ACTIVE?
  ├── Engine Prediction Matches Region?
  ├── Confidence >= Minimum Requirement?
  └── Not Selected for Drift Comparison?
     │
   YES / NO
   /     \
  ▼       ▼
Serve Local Fast Path (<0.2ms)    Execute Host Model Fallback
Persist Decision Record           Record Comparison / Shadow Outcome
```

---

## 4. Product Terminology Table

| Term | Exact Meaning |
|---|---|
| **Decision Engine** | The complete in-process system managing state contracts, candidate generation, coverage boundaries, qualification evidence, and serving authority. |
| **Internal Decision Model** | Ink-owned learned capability trained/fine-tuned for bounded categorical decision tasks. Proposes candidates; holds zero serving authority. |
| **421M Model** | Concrete neural implementation of the default policy model (`ink-decision-small`: ModernBERT-large + DecisionHead + Scorer via MLX). |
| **Exact Engine** | Ultra-fast qualified execution tier (`ExactEngine`) serving proven repeated states directly from an empirical frequency table. |
| **Sparse / Coverage Engine** | Subsystem (`CoverageEngine`) computing n-gram representations, cosine distances, and negative margins to enforce safe decision boundaries. |
| **Candidate** | An unverified choice hypothesis produced by an internal engine (`ExactEngine` or `DecisionModelEngine`). Cannot affect the host application on its own. |
| **Qualified Artifact** | A compiled, calibrated, and statistically evaluated decision artifact that has achieved empirical verification against real outcomes. |
| **Serving Authority** | The operational permission, granted only by empirical qualification evidence and coverage checks, to serve a decision locally. |
| **Host / Teacher Model** | The host application's original model/LLM path, responsible for novelty, uncertainty, exploration, and baseline comparison. |
| **Fallback** | The callable executing the host model path when a decision is not served by a qualified fast path. |

---

## 5. Storage and Operational Invariants

- **Zero External Infrastructure:** Runs in-process with embedded SQLite WAL storage (`.ink/decisions.db`). No Redis, Pinecone, or background daemons required.
- **Fail-Open Guarantee:** Storage locks, database errors, or engine exceptions fail open immediately to the host fallback without raising unexpected exceptions to the host application.
- **Write-Before-Return:** Verified fast-path decisions are durably recorded in WAL before returning to the caller.

---

## 6. Structural Decomposition

To preserve strict maintainability and single-responsibility boundaries, Ink isolates core operations across specialized runtime and internal modules:

- `ink.client.Ink`: Public SDK façade providing top-level developer APIs, lifecycle thread coordination, and clean delegation.
- `ink.runtime.dispatcher.Dispatcher`: High-throughput decision pipeline providing exact and semantic fast-path routing, adaptive comparison sampling, and fail-open host fallback dispatch with full sync/async parity.
- `ink.runtime.evaluator.Evaluator`: Artifact compilation, partition-safe candidate calibration, statistical qualification, and self-tuning maintenance.
- `ink.runtime.epoch_manager.EpochManager`: Progressive evidence accumulation epochs and per-region serving lifecycles with strict typed `StorageError` handling.
- `ink.runtime.diagnostics.Diagnostics`: Read-only inspection, status reporting, operational health scoring, fleet metrics, and verifiable decision receipts.
- `ink.internal.decision_store.DecisionStore`: Transactional SQLite storage with encapsulated schema methods and zero raw SQL in runtime modules.
- `ink.decision_api`: Minimal backward-compatibility module preserving existing public exports.
