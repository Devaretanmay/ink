# Ink — Release State

## Current Product Claim

> Ink learns where semantic representations improve compilation for a DecisionSite. Only independently qualified Exact or Linear artifacts serve locally; unresolved decisions fall back to the Host.
>
> Models propose structure. Evidence grants authority. Ink compiles what survives.

---

## 1. Current Architecture

Ink provides behavior Just-In-Time compilation for agentic decision systems. Its operational architecture strictly separates serving from learning:

### Synchronous Serving Tier (Fast Path)

```text
Request → Exact Cache → Linear Classifier → Host Fallback
```

1. **Exact Match Engine:** Constant-time deterministic lookup on verified canonical input representations.
2. **Linear Classifier Engine:** Bounded, microsecond-latency sparse linear model operating over qualified feature representations.
3. **Host Fallback:** When no local artifact clears statistical authority thresholds, execution immediately delegates to the external LLM/Host without blocking or speculative neural evaluation.

Neither `ink-decision-small` nor `ink-decision-large` ever participates in synchronous serving. Direct neural model execution in the customer request hot-path is architecturally prohibited.

### Control Plane & Compilation

```text
Classical Representations (Bag-of-Words / Lexical)
               ↕
ink-decision-small (Adaptive Semantic Representation)
               ↓
PolicyUtilityController (Fair Counterfactual Competition)
               ↓
Independent Statistical Qualification (Wilson Lower Bound vs Site Error Budget)
               ↓
Active Fast-Path Artifact (Exact / Linear)
```

1. **Semantic Proposals & Feature Extraction:** `ink-decision-small` runs strictly out-of-band in the control plane to provide semantic representations (embeddings and activation signatures) for compilation candidates.
2. **Representation Competition:** For each `DecisionSite`, the compiler evaluates classical lexical representations alongside Small semantic representations against identical selection and validation partitions.
3. **Evidence Lifecycle:** Authority belongs exclusively to the evidence lifecycle. A candidate representation is auto-enabled only when its compiled classifier achieves a Wilson confidence lower bound exceeding the site's required accuracy threshold.

### Offline Distillation Plane

```text
ink-decision-large (Teacher / Proposals)
               ↓
 Distillation Objective: 0.7 CE(verified) + 0.3 τ² KL(teacher || student)
               ↓
ink-decision-small (Student)
```

`ink-decision-large` serves purely as an offline teacher and high-parameter semantic proposal reference. It is never loaded into memory during standard application operation.

---

## 2. Canonical Models

| Property | `ink-decision-small` | `ink-decision-large` |
|---|---|---|
| **Role** | Control-plane semantic representation | Offline teacher / distillation / reference |
| **Model Version** | `1.0.0` | `1.0.0` |
| **Runtime Version** | `2` | `2` |
| **Checkpoint Revision** | `phase18-full-kd` | `gliner2.5-decide-486m` |
| **Base Architecture** | ModernBERT-large (28L/16H) + DecisionHead + Scorer | DeBERTa-v3-large + GLiNER2.5 Decide multi-label head |
| **Parameter Count** | 421,293,830 (~421M) | 486,444,053 (~486M) |
| **Weight Precision** | `float16` | `float32` |
| **Backend** | MLX (Metal GPU / Apple Silicon / Linux CPU) | PyTorch / GLiNER |
| **Weights SHA-256** | `28e7e65bfbd272c6392c52616bbb409a7a425db604df442e2682ab0155593276` | `40a5a23ff860dc3dff426cecd1048cacdd29c648c96db209dad818e9686dc997` |
| **Manifest SHA-256** | `f1ee15bdcc840e91f649e6eefe2c7dc3d1c7779cecc66215a01fccc10c02de08` (`rl_agent_config`) | `501c6579278518e72dbba5a1f248b421dd5bfb13600db45d86c94c224368670b` (`config`) |
| **Serving Authority** | **0%** (Proposals & representation only) | **0%** (Offline distillation only) |
| **Default Model** | Yes | Optional / offline |
| **License** | Ink Internal / Pinned (see NOTICE) | Apache-2.0 |

---

## 3. Safety & Qualification Contract

Serving authority is governed by a strict statistical safety contract:

1. **Independence:** Candidate selection and qualification happen on strictly separated observation partitions to prevent optimization optimism.
2. **Wilson Lower Bound Gating:** Point accuracy is never sufficient. A candidate classifier is granted serving authority if and only if its conservative 95% Wilson confidence score lower bound meets or exceeds the site's required accuracy:
   $$\text{WilsonLower}(k, n, \alpha=0.05) \ge 1.0 - \text{site\_error\_budget}$$
3. **State Discrimination:** The controller cleanly distinguishes between:
   - `INSUFFICIENT_EVIDENCE`: Promising candidate gathering further verification data before serving eligibility.
   - `QUALITY_FAIL`: Candidate demonstrably violates the site error budget and is denied promotion.
4. **Post-Activation Monitoring & Revocation:** Every local serve is tracked against subsequent verifier outcomes. If empirical serving accuracy breaches the error budget or drift is detected, serving authority is immediately demoted back to Host fallback.

---

## 4. Current Validation

### Validated Internal Evidence

- **Tool Routing Workload:** Under controlled internal qualification benchmarks, `ink-decision-small` semantic representation produced qualified linear artifacts clearing the site safety threshold ($W_{\text{lower}} \ge 0.99$), reducing Host calls with zero false serves.
- **Support Routing Workload:** Small representation demonstrated competitive utility and passed statistical qualification under its target error budget.
- **Fair Counterfactuals:** Controlled workloads verified that classical baseline features also receive fair evaluation under identical safety budgets, ensuring Small is enabled only where it confers measurable structural advantage.

### What Is NOT Yet Validated

- **External Production Validation:** End-to-end design-partner deployment against live customer production traffic is **not yet performed**.
- **Real Host API Cost/Latency Trials:** Historical simulations used modeled Host costs; physical upstream API latency and dollar savings have not been measured against live commercial endpoints in production.
- **Site Generality:** Small representations do not confer universal benefit across all decision topologies. On highly structured or sparse classification spaces, classical representations remain preferred or equivalent.

---

## 5. Known Limitations

1. **Resident Memory:** When initialized, `ink-decision-small` weights remain resident in memory for control-plane tasks (~840MB memory footprint).
2. **No Synchronous Neural Serving:** Users expecting direct LLM or neural token generation will not find it in the fast path; Ink compiles categorical decision boundaries into lightweight artifacts.
3. **Offline Distillation Environment:** Distillation of Small from Large requires an environment equipped with PyTorch and GLiNER dependencies.

---

## 6. Cleanup & Repository Hardening

In the transition from experimental research to release-hardened product:

- Removed all historical Phase 1–22E experimental directories, temporary runs, and interim benchmark logs.
- Removed legacy uncommitted datasets, cache corpora, and teacher logit snapshots.
- Relocated canonical Small checkpoint metadata to clean package storage and eliminated duplicate weight files.
- Purged obsolete benchmarks and invalidated general claims (such as single-domain 91.18% figures).
- Pinned and validated the statistical serving gate in production compiler code.

---

## 7. Release & Validation Status

- **Internal Architecture Validation:** Completed and verified.
- **External Design-Partner / Live Production Validation:** Pending (next milestone).
- **Core CI Status:** Clean, passing all brand, product, and repo hygiene audits, full unit and integration test suites, build packaging, and wheel validation.

---

## 8. Thermonuclear Code Review

- **Status:** Completed under Thermonuclear Code Quality Review standard.
- **Blocker Findings:** 0
- **High Findings:** 0
- **Fixes Applied:**
  - Removed `phase18/` dependency in `test_phase19_policy_models.py` and canonicalized model verification.
  - Added `python/ink/ink/client.py` database migration facade to hygiene allowlists.
  - Resolved `E402` module-level import ordering in `python/ink/ink/__init__.py` and `internal/contracts.py`.
  - Resolved `UP035` and unused variables in `internal/coverage.py` and `internal/selection.py`.
  - Resolved loop variable binding `B023` in `runtime/evaluator.py`.
  - Hoisted inline `json` import to module top-level in `runtime/utility_controller.py`.
  - Purged 13.6GB+ of uncommitted/cached experimental datasets, duplicate checkpoints, and blobs.
- **Remaining Accepted Issues:**
  - `internal/decision_store.py` exceeds 1k lines (~1,620 lines) as the unified relational persistence boundary for SQLite transactional integrity.

