# Architecture

Ink separates decision execution from decision learning.

Serving decisions locally requires sub-millisecond execution. Learning and qualifying behavior requires statistical rigor. Ink isolates these concerns across three planes.

---

## The Three Planes

```text
SERVING PATH (Synchronous, Sub-Millisecond)

         Request
            │
            ▼
      Exact Match? ──────► Served Locally (< 0.01ms, $0)
            │ miss
            ▼
     Linear Boundary? ───► Served Locally (< 0.10ms, $0)
            │ miss / novel
            ▼
       Host Model ───────► Remote LLM Fallback
```

```text
CONTROL PLANE (Asynchronous, Background Compilation)

     Verified Outcomes (SQLite)
                 │
                 ▼
     Classical Representation
                 ↕ competition
     Small Policy Representation (ink-decision-small)
                 │
                 ▼
       Candidate Compiled Artifact
                 │
                 ▼
     Statistical Qualification Gate (Wilson Lower Bound >= Error Budget)
                 │
                 ▼
          Serving Authority Granted (ACTIVE)
```

```text
OFFLINE DISTILLATION (Development & Training Only)

     ink-decision-large (Teacher)
                 │ distillation
                 ▼
     ink-decision-small (Student)
```

---

## 1. Serving Path

The serving path executes synchronously inside the application process.

### Tier 1: Exact Match (`ExactEngine`)
- Direct hash lookups against verified state fingerprints.
- Runs in $< 0.01\text{ ms}$ with zero network overhead.

### Tier 2: Linear Boundary (`LinearClassifierEngine`)
- Sparse lexical or dense semantic linear models.
- Resolves repeated semantic boundaries in $< 0.10\text{ ms}$.

### Fallback: Host
- If a state is unseen, outside coverage, or ambiguous, Ink calls the Host immediately.
- Ink fails open to the Host. An unverified artifact never guesses in production.

---

## 2. Control Plane

The control plane compiles candidates and manages serving authority.

- **Representation Competition**: Ink tests classical lexical features against `ink-decision-small` semantic proposals.
- **Quality Gating**: Ink enables `ink-decision-small` only on DecisionSites where its representations measurably improve qualified coverage without exceeding the site error budget.
- **Statistical Safety Gate**: Candidates must clear the Wilson 95% confidence lower-bound threshold.
- **Deoptimization**: If verified outcomes drop below the required error budget, Ink demotes the artifact and returns traffic to the Host.

---

## 3. Model Roles

Ink defines two specialized models:

| Model | Parameters | Role | Serving Authority |
| :--- | :--- | :--- | :--- |
| `ink-decision-small` | 421M | Control plane semantic representation proposals | **None** (Never serves directly) |
| `ink-decision-large` | 486M | Offline distillation teacher | **None** (Offline only) |

Neither model is in the synchronous serving path. Serving authority belongs exclusively to qualified Exact and Linear artifacts.

---

## 4. Persistence & Failure Mode

- **Storage**: All contracts, decisions, and outcomes persist locally in SQLite (`.ink/decisions.db`) using WAL mode.
- **Fail-Open Invariant**: If SQLite is locked, unreadable, or corrupted, Ink defaults directly to the Host model. Application uptime is always preserved.
