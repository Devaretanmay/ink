# Architecture

Ink separates decision execution from decision learning.

Production applications need sub-millisecond serving without network round-trips. Learning, representation search, and statistical qualification require thorough background analysis.

Ink organizes these responsibilities across four distinct planes:

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

The serving plane is synchronous and runs directly inside your application process.

### Serving flow
1. **Request intake:** Application calls `ink.decide(site, state, fallback=...)` or uses the `@ink.wrap` decorator.
2. **Fast Path evaluation:**
   - **Exact match (`ExactEngine`):** Looks up hash fingerprints over structured states in $< 0.01\text{ ms}$.
   - **Linear boundary (`LinearClassifierEngine`):** Evaluates sparse lexical features or dense projections in $< 0.10\text{ ms}$.
3. **Coverage check:** Ink checks whether the incoming state falls inside a qualified, high-density decision region.
4. **Host fallback:** If the state is unseen, outside coverage, ambiguous, or if no qualified artifact is active, Ink runs your Host model fallback immediately.

### The fail-open invariant
Ink routes to the Host fallback if any runtime or storage failure (such as a database lock or schema mismatch) occurs. Your production application never crashes or stalls because of Ink.

---

## 2. The Evidence Plane

The evidence plane maintains an append-only, durable record of system behavior in local SQLite storage (`.ink/decisions.db`):

- **State records:** Hashes and serialized inputs for all observed decisions.
- **Outcome records:** Ground-truth feedback (`quality`, `verifier`, `verifier_version`, timestamp).
- **Lineage records:** Checkpoints of compiled artifacts and their training data slices.
- **Evaluation records:** Complete historical qualification trials, sample counts ($n$), errors ($k$), and Wilson confidence intervals.

Write-Ahead Logging (`PRAGMA journal_mode = WAL`) ensures non-blocking concurrent reads during high-throughput serving.

---

## 3. The Compiler Plane

The compiler plane runs in the background (or during dedicated maintenance runs) and trains candidate local execution artifacts.

### Representation competition
During compilation, Ink tests multiple representations against each other:
1. **Classical lexical features:** Sparse n-grams, exact structured tokens, and statistical key frequencies.
2. **Small policy representations (`ink-decision-small`):** Dense semantic embeddings proposed by a specialized 421M parameter ModernBERT-large model running locally on Apple Silicon Metal or Linux CPU.

### Quality-aware utility gating
`ink-decision-small` does **not** automatically win compilation. The compiler's utility controller enables Small-derived features only if they demonstrably expand qualified coverage without degrading accuracy on independent validation sets.

### Zero direct serving authority
`ink-decision-small` operates **only** in the compiler plane. It never intercepts synchronous production queries. Serving authority belongs exclusively to compiled Exact and Linear artifacts.

---

## 4. The Authority Plane

The authority plane governs whether a candidate artifact is permitted to serve live user traffic.

### The qualification gate
Before an artifact moves from `SHADOW` to `ACTIVE`, it must pass an independent statistical test on held-out evaluation traffic:

$$\text{Wilson Lower Bound}(k, n) \ge 1.0 - \text{local\_error\_budget}$$

Where:
- $n$ is the count of independent evaluation observations.
- $k$ is the count of verified errors.
- $\text{local\_error\_budget}$ is the site's tolerated error rate (e.g. 1% or 5%).

If point accuracy is 100% on only 10 samples, the 95% Wilson lower bound is only ~72%. Ink refuses serving authority until sufficient evidence accumulates.

### Continuous comparison & revocation
After activation, Ink continues to verify behavior:
- A randomized comparison sample (default 5%) executes your Host model in parallel.
- If verified error rates exceed the site error budget during evaluation, Ink revokes serving authority (`ACTIVE` $\to$ `SHADOW`).
- Subsequent requests route to the Host model until new evidence is accumulated and qualified.

---

## Model inventory & roles

| Model | Architecture | Parameters | Plane | Live Serving Authority |
| :--- | :--- | :--- | :--- | :--- |
| **`ink-decision-small`** | ModernBERT-large + Multi-task Heads | 421M | Compiler Plane (Background) | **None** (Proposals only) |
| **`ink-decision-large`** | DeBERTa-v3-large | 486M | Offline Distillation (Research) | **None** (Offline only) |

Neither model executes in the synchronous serving path. Ink's serving path relies exclusively on compiled, deterministic, and linear local software artifacts.
