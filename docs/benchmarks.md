# Benchmark Evidence & Validation Hierarchy

This document defines Ink's evidence hierarchy, reports current empirical validation results, and explains how to reproduce them.

---

## 1. The Evidence Hierarchy

To maintain technical integrity, Ink categorizes all performance and safety data across five explicit tiers:

```text
Level 1: Invariant & Unit Validation
Level 2: Controlled Internal Workloads
Level 3: Frozen Reproducible Evaluations  ◄── CURRENT HIGHEST COMPLETED LEVEL
Level 4: External Design-Partner Validation (In Progress)
Level 5: Real Production Fleet Evidence (Future)
```

| Tier | Definition | Current Status |
| :--- | :--- | :--- |
| **Level 1: Invariant & Unit Validation** | Automated tests verifying system boundaries, fail-open behavior, and mathematical confidence calculations in isolation. | **Verified** (The current CI executes 266 automated tests across Linux/macOS and Python 3.11–3.13) |
| **Level 2: Controlled Internal Workloads** | Synthetic and historical trace datasets simulating agent tool routing, support triage, and incident classification. | **Verified** (Evaluated across Exact and Linear engines) |
| **Level 3: Frozen Reproducible Evaluations** | Deterministic benchmark runs with frozen random seeds, fixed error budgets, and independent holdout data. | **Verified** (Reproducible via benchmark harness) |
| **Level 4: External Design-Partner Validation** | Pilot deployments instrumented directly in partner production systems across diverse business domains. | **In Progress** |
| **Level 5: Real Production Fleet Evidence** | Long-horizon public telemetry and audited case studies from multi-tenant production fleets. | **Future** |

Ink does not claim Level 4 or Level 5 evidence today. The strongest completed evidence level is **Level 3 (Frozen Reproducible Evaluations)**.

---

## 2. Controlled Evaluation Results (Level 2 & Level 3)

The following measurements reflect controlled internal evaluations across three canonical DecisionSites:
1. **Tool Routing**: Agent tool selection across structured arguments and natural language intents.
2. **Support Triage**: Customer ticket classification into discrete department queues.
3. **Incident Management**: Infrastructure alert routing by severity and affected subsystem.

### Empirical Results Summary

| Workload | Serving Engine | Evaluated Traffic | Local Fast Path Serving Rate | Holdout Error Rate | 95% Wilson Confidence Lower Bound | Required Site Error Budget | Qualification Outcome |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Tool Routing** | Linear | 319 | 78.4% | 0.0% | 98.49% | $\le 2.0\%$ | **ACTIVE** |
| **Support Triage** | Exact | 300 | 42.3% | 0.0% | 97.06% | $\le 5.0\%$ | **ACTIVE** |
| **Incident Triage** | Exact | 300 | 55.6% | 0.0% | 97.75% | $\le 5.0\%$ | **ACTIVE** |

### Key Empirical Findings

1. **Selective Coverage with Zero Forced Serves**:
   The local engines achieved between 42.3% and 78.4% local serving coverage. Requests outside high-density coverage regions were not forced to serve locally; they cleanly fell back to the Host model.
2. **Error Budget Enforcement**:
   No candidate artifact received serving authority until its Wilson 95% confidence lower bound cleared the site's required error budget ($1.0 - \text{budget}$). In all qualified holdout trials, local error rates remained at 0.0%.
3. **Serving Latency**:
   - `ExactEngine` (hash fingerprint): Typically $< 0.01\text{ ms}$ per decision.
   - `LinearClassifierEngine` (sparse/dense weights): Typically $< 0.10\text{ ms}$ per decision.
   - Local decisions execute synchronously in memory on host CPU or Apple Silicon Metal without network round-trips.

---

## 3. What Is NOT Claimed

To avoid misleading claims common in AI infrastructure marketing, Ink explicitly states:

- **Ink does not claim "zero risk" or "absolute correctness."** Real-world distributions can drift; Ink claims statistical governance and continuous deoptimization, not omniscience.
- **Ink does not claim universal 100% local coverage.** Most real-world workloads contain genuine novelty. Real-world local coverage will depend on workload recurrence, verifier quality, and the configured error budget; forcing 100% coverage causes errors.
- **Ink does not claim universal cost savings.** Cost reduction depends on traffic recurrence, the cost of your Host model, and whether your error budget allows local qualification. If a workload does not repeat, Ink saves zero calls.

---

## 4. How to Reproduce

You can reproduce the controlled evaluations and generate the machine-readable Level 3 evidence manifest directly:

```bash
# Generate frozen evidence manifest across canonical workloads
python scripts/generate_evidence_manifest.py

# Or run the full competitive frontier sweeps across all execution arms
PYTHONPATH=python/ink python -m benchmarks --workload all --arms all
```

The evaluation runner executes the full qualification pipeline:
1. Observes training traffic and records ground truth outcomes
2. Compiles candidate representations (Exact / Linear)
3. Calibrates in shadow mode against holdout partitions
4. Verifies empirical error rates against explicit site error budgets
5. Simulates serving execution, audits comparison traffic, and writes `benchmarks/results/evidence_manifest.json`
