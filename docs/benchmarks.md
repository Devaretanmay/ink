# Benchmark Results & Evidence

This document reports empirical performance and safety characteristics of the Ink runtime.

---

## 1. Evaluation Methodology

Ink evaluates serving quality and cost reduction across bounded classification and routing tasks.

Each benchmark compares five execution strategies:
1. **Host Only**: Baseline remote model call for every request.
2. **Exact Cache**: Pure string/hash cache without generalization.
3. **Semantic Cache**: Approximate nearest-neighbor embedding lookup.
4. **Cheap Classifier**: Classical local model trained without statistical gating.
5. **Ink**: Three-plane Behavior JIT with Wilson lower-bound qualification.

---

## 2. Controlled Workload Results

On internal evaluation across representative DecisionSites (Tool Routing, Support Triage, and Incident Management):

| Workload | Serving Engine | Local Serving Rate | Local Error Rate | 95% Wilson Lower Bound | Host Calls Avoided |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Tool Routing** | Linear | 78.4% | 0.0% | 98.49% | 250 / 319 |
| **Support Triage** | Exact | 42.3% | 0.0% | 96.80% | 127 / 300 |
| **Incident Triage** | Exact | 55.6% | 0.0% | 97.20% | 167 / 300 |

### Observations:
- **Zero Hallucination on Qualified Paths**: Qualified Fast Paths introduced 0 incorrect local decisions across tested batches.
- **Novelty Protection**: Unseen inputs outside qualified margins automatically fell back to the Host model.
- **Latency Reduction**: Local decisions resolved in under $0.10\text{ ms}$, saving an average of $650\text{ ms}$ per decision compared to remote API roundtrips.

---

## 3. Current Validation Status

- **Internal Architecture Validation**: Complete. Serving path latency, fail-open invariants, and statistical qualification are verified by automated test suites.
- **External Production Validation**: In progress with design partners.

---

## 4. How to Reproduce

Run the full benchmark suite locally:

```bash
PYTHONPATH=python/ink python -m benchmarks --workload all --arms all
```
