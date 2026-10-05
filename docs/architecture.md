# Ink Architecture

Ink is a **behavior JIT for production AI**. It intercepts repetitive, bounded agent decisions and executes them locally in sub-milliseconds with mathematical safety guarantees, falling back seamlessly to the host application's original model whenever novelty, uncertainty, or policy drift occurs.

```text
Application decision request
          │
          ▼
┌──────────────────────────────┐
│ Ink Decision Engine    │
│                              │
│  1. Contract / state         │
│  2. Exact learned behavior   │
│  3. Internal learned model   │
│  4. Coverage / regions       │
│  5. Qualification evidence   │
│  6. Serving authority        │
└──────────────┬───────────────┘
               │
        Can Ink safely
        serve this decision?
          /            \
        yes             no
        │                │
        ▼                ▼
 local qualified     original host
    decision        model / fallback
```

---

## 1. The Three Cardinal Concepts

The architecture strictly distinguishes three concepts:

1. **Model Capability:** Ink's owned learned representation and decision prediction capability. Ink conceptually includes an internal learned decision model trained specifically for bounded decision tasks.
2. **Serving Engine:** The concrete execution mechanism used to generate a candidate choice for an incoming state:
   - `ExactEngine`: Ultra-fast frequency table execution tier bypassing heavier inference for proven exact states.
   - `CoverageEngine`: Sparse TF-IDF representation measuring distance to verified prototype states.
   - `DecisionModelEngine` (`MlxDecisionEngine`): Concrete 421M ModernBERT neural backend on Apple Silicon / Linux.
3. **Serving Authority:** Operational permission to replace the host model fallback for a specific decision request. Serving authority is held exclusively by the qualification system, never by candidate-generating engines.

```text
candidate != authority
```

---

## 2. Architectural Invariants

### Invariant 1 — Ink owns a learned decision model
The Decision Engine conceptually includes a Ink-owned learned decision model trained and fine-tuned specifically for bounded categorical decisions. Ink is not merely a cache or wrapper; learned decision modeling is a core architectural component.

### Invariant 2 — Serving authority is separate from candidate production
A candidate emitted by an internal learned model or exact engine has **zero serving authority** on its own. Serving authority requires:
1. An active, integrity-verified artifact.
2. The incoming state strictly falling within qualified coverage boundaries.
3. Model confidence meeting or exceeding site promotion requirements.
4. Passing non-comparison traffic checks.
If any condition is not met, the host application's original fallback model executes.

### Invariant 3 — The 421M model is the default implementation, not the architecture
The current ~421M neural model (ModernBERT-large with custom DecisionHead and Scorer) is Ink-owned work and ships as a default part of every standard installation. It can be disabled on resource-constrained deployments (`model_enabled=False`). The architecture permits future learned backends (distilled, quantized, linear, hybrid) without altering core contracts.

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
| **421M Model** | Concrete neural implementation of the internal decision model (`ink-decision-v1`: ModernBERT-large + DecisionHead + Scorer via MLX). |
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
