# Architecture

Ink separates decision execution from decision learning.

Production applications require sub-millisecond serving without network round-trips. Learning, representation search, and statistical qualification require thorough background analysis.

Ink organizes these responsibilities into four distinct planes:

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        1. SERVING PLANE                                │
│                     (Synchronous, In-Process)                          │
│                                                                        │
│   Request ──► Qualified Local Fast Path (< 0.10ms)                     │
│                   │ miss / novel / outside coverage                    │
│                   ▼                                                    │
│               Host Model Fallback (Your LLM)                           │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ states, choices, outcomes
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        2. EVIDENCE PLANE                               │
│                   (Local SQLite Ledger, WAL Mode)                      │
│                                                                        │
│   Decision Records  •  Verified Outcomes  •  Audit Trail  •  Lineage   │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ historical evidence
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        3. COMPILER PLANE                               │
│                  (Asynchronous, Background Tasks)                      │
│                                                                        │
│   Classical Lexical Features  ◄──►  ink-decision-small Representations │
│                                  │                                     │
│                                  ▼                                     │
│                     Candidate Artifact Training                        │
│                   (ExactEngine or LinearClassifier)                    │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ candidate artifact
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        4. AUTHORITY PLANE                              │
│                    (Statistical Governance Gate)                       │
│                                                                        │
│   Wilson 95% Lower Bound Evaluation  •  Error Budget Enforcement       │
│                                  │                                     │
│             PASS                 │                 FAIL                │
│    Grant Serving Authority       │       Retain Site in EVALUATING     │
│       (SHADOW ──► ACTIVE)        │         or Revoke Authority         │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 1. The Serving Plane

The serving plane is synchronous and executes inside your application process.

### Serving Flow
1. **Request Intake**: Application calls `ink.decide(site, state, fallback=...)`.
2. **Fast Path Evaluation**:
   - **Exact Match (`ExactEngine`)**: Evaluates hash fingerprints over structured states in $< 0.01\text{ ms}$.
   - **Linear Boundary (`LinearClassifierEngine`)**: Evaluates sparse lexical features or dense projections in $< 0.10\text{ ms}$.
3. **Coverage Check**: Ink determines whether the incoming state falls inside a qualified, high-density decision region.
4. **Host Fallback**: If the state is unseen, outside coverage, ambiguous, or if no qualified artifact is active, Ink executes your Host fallback function immediately.

### Fail-Open Resilience Invariant
Ink routes to the Host fallback when supported runtime or storage failures (such as a database lock or schema mismatch) prevent local execution.

---

## 2. The Evidence Plane

The evidence plane maintains an append-only, durable record of system behavior in local SQLite storage (`.ink/decisions.db`):

- **State Records**: Hashes and serialized inputs for all observed decisions.
- **Outcome Records**: Ground truth feedback (`quality`, `verifier`, `verifier_version`, timestamp).
- **Lineage Records**: Checkpoints of compiled artifacts and their training data slices.
- **Evaluation Records**: Complete historical qualification trials, sample counts ($n$), errors ($k$), and Wilson confidence intervals.

Write-Ahead Logging (`PRAGMA journal_mode = WAL`) ensures non-blocking concurrent reads during high-throughput serving.

---

## 3. The Compiler Plane

The compiler plane runs in the background (or during dedicated maintenance cycles) and compiles candidate local execution artifacts.

### Representation Competition
During compilation, Ink tests multiple representations against each other:
1. **Classical Lexical Features**: Sparse n-grams, exact structured tokens, and statistical key frequencies.
2. **Small Policy Representations (`ink-decision-small`)**: Dense semantic embeddings proposed by a specialized 421M parameter ModernBERT-large model running locally on Apple Silicon Metal or Linux CPU.

### Quality-Aware Utility Gating
`ink-decision-small` does **not** automatically win compilation. The compiler's utility controller enables Small-derived features only if they demonstrably expand qualified coverage without degrading accuracy on independent validation sets.

### Zero Direct Serving Authority
`ink-decision-small` operates **only** in the compiler plane. It never intercepts synchronous production queries. Serving authority belongs exclusively to compiled Exact and Linear artifacts.

---

## 4. The Authority Plane

The authority plane governs whether a candidate artifact is permitted to serve live user traffic.

### The Qualification Gate
Before an artifact moves from `SHADOW` to `ACTIVE`, it must pass an independent statistical test on held-out evaluation traffic:

$$\text{Wilson Lower Bound}(k, n) \ge 1.0 - \text{Error Budget}$$

Where:
- $n$ is the count of independent evaluation observations.
- $k$ is the count of verified errors.
- $\text{Error Budget}$ is the site's tolerated error rate (e.g. 1% or 5%).

If point accuracy is 100% on only 10 samples, the 95% Wilson lower bound is only ~72%. Ink refuses serving authority until sufficient evidence accumulates.

### Continuous Comparison & Revocation
After activation, Ink continues to verify behavior:
- A randomized comparison sample (default 5%) executes the Host model in parallel.
- If verified error rates exceed the site error budget during evaluation, Ink revokes serving authority (`ACTIVE` $\to$ `SHADOW`).
- Subsequent requests route to the Host model until new evidence is accumulated and qualified.

---

## Model Inventory & Roles

| Model | Architecture | Parameters | Plane | Live Serving Authority |
| :--- | :--- | :--- | :--- | :--- |
| **`ink-decision-small`** | ModernBERT-large + Multi-task Heads | 421M | Compiler Plane (Background) | **None** (Proposals only) |
| **`ink-decision-large`** | DeBERTa-v3-large | 486M | Offline Distillation (Research) | **None** (Offline only) |

Neither model executes in the synchronous serving path. Ink's serving path relies exclusively on compiled, deterministic, and linear local software artifacts.
