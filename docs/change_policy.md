# Ink Change Control Policy

**Effective Date:** October 9, 2026  
**Scope:** Core Runtime, Public SDK, CLI, Console, Engine Subsystems  

Following the completion and sign-off of Phase 14, **the core behavior and architecture of Ink are FROZEN**.

No internal feature experiments, benchmark-driven optimizations, unprompted engine additions, or speculative UX enhancements may be committed. All changes must be explicitly categorized under one of the labels defined below and must be justified by empirical evidence from real external users or design partners.

---

## Change Labels and Criteria

### 1. `BUG`
- **Definition:** The runtime behavior is objectively incorrect, violates an invariant, or crashes under documented usage.
- **Requirement:** Must include a reproducible, failing test case demonstrating the defect prior to the fix. Must touch only the minimum lines needed to fix the defect without speculative refactoring.

### 2. `SAFETY`
- **Definition:** An issue where authority, fallback, OOD detection, or deoptimization behaves unsafely, potentially allowing unproven or invalid local Fast Paths to execute.
- **Requirement:** Highest priority. Requires formal Hoeffding / verification analysis, reproducible reproduction script, and regression test.

### 3. `INTEGRATION`
- **Definition:** A real external design partner cannot reasonably integrate Ink into their production application using the public SDK (`ink.decide()`, `ink.record_outcome()`).
- **Requirement:** Must link to a specific design partner ticket, issue, or observed integration roadblock. Requires public documentation update.

### 4. `DX` (Developer Experience)
- **Definition:** A real developer is able to integrate, but the developer experience is unnecessarily abrasive, cryptic, or error-prone (e.g. confusing exception tracebacks, CLI parameter ambiguity).
- **Requirement:** Must cite developer feedback from an actual design-partner onboarding session.

### 5. `CUSTOMER REQUEST`
- **Definition:** A design partner specifically requests a capability that has obvious, credible reuse across multiple production teams (e.g. a specific telemetry export hook or environment configuration).
- **Requirement:** Requires written evidence of demand from $\ge 1$ external team and explicit confirmation that it does not degrade safety or core Behavior JIT invariants.

### 6. `EXPERIMENTAL`
- **Definition:** Research or exploratory prototypes conducted strictly outside the production path (e.g., in `experiments/`).
- **Requirement:** Must NOT alter or monkey-patch the frozen production runtime (`python/ink/ink/runtime/`, `decision_api.py`, `internal/`).

---

## Prohibited Modifications

The following actions are strictly prohibited without an explicit change of phase or unfreezing mandate:
1. Re-tuning Hoeffding bounds or qualification thresholds to arbitrarily boost coverage.
2. Granting local execution authority based solely on model confidence, semantic cosine similarity, or unverified consensus.
3. Adding new local classifier engines or ML dependencies unless driven by an active pilot requirement.
4. Building multi-tenant cloud infrastructure (SaaS billing, auth/SSO, RBAC, hosted dashboards) prior to customer pull.
5. Large-scale refactoring or opportunistic cleanup during bug fixes.
