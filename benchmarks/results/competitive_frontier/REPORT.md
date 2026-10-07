# Competitive Safety × Savings Frontier Benchmark Report

**Benchmark suite version:** Ink v0.5.0
**Evaluation date:** 2026-10-02
**Dataset scale:** 18,000 decisions across 4 workloads (3 candidate workloads + 1 negative control)
**Evaluation protocol:** strict temporal split (70% history / 30% future evaluation), zero future leakage.

> **Scope and labeling.** This is a **controlled, synthetic** benchmark. Workloads and
> policy-drift injections are generated for reproducibility, not sampled from a customer
> deployment. Results describe behavior on these workloads only and do not transfer
> unchanged to production traffic. See [docs/claims.md](../../../docs/claims.md) for every
> claim, its evidence path, and its scope.

---

## 1. Summary

The question this benchmark asks:

> At the same level of model-call reduction, does Ink produce fewer incorrect production
> decisions than obvious alternatives (exact cache, semantic cache, cheaper model, smaller
> classifier)?

Findings on repetitive, verifiable decision sites:

1. **Under injected passive policy drift,** semantic caches produced a **12.0%–18.0%**
   verified wrong-serve rate because they matched prototypes calibrated on stale policy.
   Ink, maintaining comparison traffic and outcome verification, **demoted the stale
   artifact after 3 consecutive disagreements** and incurred **7–8 wrong serves before
   revocation** (a **2.26%–2.68%** verified error rate; Wilson 95% CI `[1.10%, 5.19%]`).
2. **Whole-application realism.** Bounded decision sites were 15%–25% of total model calls;
   Ink avoided **3.99%–10.10%** of whole-application calls and **3.51%–8.87%** of
   whole-application spend.
3. **Cheaper models** achieved high call reduction but carried a persistent baseline error
   rate (11%–14%), while Ink kept verified error in the low single digits on qualified
   Fast Paths.
4. **Negative control.** On a high-entropy research workload, Ink refused compilation and
   served nothing (0% false serves). Semantic caching served with a 2.56% wrong-serve rate.

---

## 2. Workloads

1. **`support`** — support ticket action routing; 5,000 decisions; choices
   `("refund", "request_info", "specialist")`. Injected policy shift at eval step 600
   (damaged items require specialist review instead of auto-refund).
2. **`tool_select`** — agent tool selection; 5,000 decisions; choices
   `("search_docs", "database_lookup", "ask_user", "finish")`. Injected drift requires user
   confirmation for database ledger queries.
3. **`incident_triage`** — incident escalation; 5,000 decisions; choices
   `("auto_mitigate", "page_oncall", "file_ticket", "suppress")`. Injected drift updates
   memory-leak remediation policy.
4. **`research_novelty`** — negative control; 3,000 decisions; open-ended literature
   queries with unique session IDs, high entropy, near-zero repetition (< 2%).

## 3. Temporal structure

- **First 70%:** history / calibration / qualification.
- **Last 30%:** strict future evaluation.
- Internal eval structure: `0–300` stable, `300–600` paraphrase expansion, `600–900`
  injected policy drift, `900–1200` post-drift stable, `1200–1500` recovery.

## 4. Market boundary

| Workload | All app LLM calls | Bounded decision calls | Bounded + verifiable | Ink active coverage |
| :--- | :---: | :---: | :---: | :---: |
| **support** | 25,000 | 5,000 (20.0%) | 5,000 (20.0%) | 299 (19.9% of eval) |
| **tool_select** | 20,000 | 5,000 (25.0%) | 5,000 (25.0%) | 310 (20.7% of eval) |
| **incident_triage** | 20,000 | 5,000 (25.0%) | 5,000 (25.0%) | 606 (40.4% of eval) |
| **research_novelty** | 21,000 | 3,000 (14.3%) | 600 (2.9%) | 0 (rejected) |

Bounded verifiable decisions were roughly **15%–25%** of total calls. Ink accelerates the
**bounded decision layer**, not the whole application.

## 5. Arms

1. **Original model** — teacher baseline (~125–145 ms latency).
2. **Exact cache** — canonical JSON hashing; swept observation counts (1, 2, 3, 5) and TTL.
3. **Semantic cache** — TF-IDF n-gram vectorizer + cosine similarity; threshold sweep 0.70–0.99.
4. **Cheaper model** — lower-cost tier (~30–38 ms latency); confidence-threshold sweep.
5. **Small classifier** — NumPy TF-IDF naive Bayes trained on the history split.
6. **Ink** — full lifecycle (observe → compile → calibrate → shadow → active → demote →
   requalify); comparison-rate sweep (0.05, 0.10, 0.20) and confidence thresholds.

## 6. Tuning sweeps

- Semantic cache: thresholds `0.70, 0.75, 0.80, 0.85, 0.90, 0.925, 0.95, 0.975, 0.99`.
- Cheaper model: confidence gates `0.0, 0.60, 0.70, 0.80, 0.85, 0.90, 0.95`.
- Small classifier: `0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.98`.
- Ink: comparison rates `0.05, 0.10, 0.20` and confidence `0.90, 0.95, 0.98`; default `0.10 / 0.95`.

## 7. Outcome correctness

### Workload 1: support

| Arm | Configuration | Local serves | Wrong serves | Wrong-serve rate | Wilson 95% CI | Teacher agreement |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| Original model | teacher | 0 | 0 | 0.00% | N/A | 100.0% |
| Exact cache | min_obs=2 | 736 | 117 | 15.90% | [13.43%, 18.71%] | 91.47% |
| Semantic cache | sim=0.85 | 1500 | 270 | 18.00% | [16.14%, 20.03%] | 80.53% |
| Cheaper model | always | 0 | 0 | 0.0% (model err 11.87%) | N/A | 86.53% |
| Small classifier | conf=0.80 | 1500 | 270 | 18.00% | [16.14%, 20.03%] | 80.53% |
| **Ink** | default (0.10/0.95) | **299** | **8** | **2.68%** | **[1.36%, 5.19%]** | **98.80%** |

### Workload 2: tool_select

| Arm | Configuration | Local serves | Wrong serves | Wrong-serve rate | Wilson 95% CI | Teacher agreement |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| Original model | teacher | 0 | 0 | 0.00% | N/A | 100.0% |
| Exact cache | min_obs=2 | 806 | 130 | 16.13% | [13.75%, 18.83%] | 90.67% |
| Semantic cache | sim=0.85 | 1500 | 270 | 18.00% | [16.14%, 20.03%] | 80.80% |
| Cheaper model | always | 0 | 0 | 0.00% (model err 13.80%) | N/A | 84.93% |
| Small classifier | conf=0.80 | 1500 | 270 | 18.00% | [16.14%, 20.03%] | 80.80% |
| **Ink** | default (0.10/0.95) | **310** | **7** | **2.26%** | **[1.10%, 4.59%]** | **99.00%** |

## 8. Savings: DecisionSite vs. whole-application

| Workload | Arm | Site call reduction | Whole-app call reduction | Whole-app spend reduction | Net savings |
| :--- | :--- | :---: | :---: | :---: | :---: |
| support | Exact cache (min=2) | 49.07% | 9.81% | 9.75% | $0.2940 |
| support | Semantic cache (0.85) | 100.0% | 20.0% | 20.0% | $0.6031 |
| support | **Ink (default)** | 19.93% | **3.99%** | **3.51%** | **$0.1058** |
| tool_select | Exact cache (min=2) | 53.73% | 13.43% | 13.38% | $0.4949 |
| tool_select | Semantic cache (0.85) | 100.0% | 25.0% | 25.0% | $0.9247 |
| tool_select | **Ink (default)** | 20.67% | **5.17%** | **4.73%** | **$0.1749** |

## 9. Overhead

- Ink qualification overhead: ~$0.005 per candidate site.
- Ink comparison-traffic cost: ~$0.021 (10% sampling of active traffic).
- Ink is net positive within roughly **35–65 decisions** of evaluation start.

## 10. Latency (support workload)

| Arm | Local serve p50 (ms) | Local serve p95 (ms) | Fallback p95 (ms) |
| :--- | :---: | :---: | :---: |
| Original model | N/A | N/A | 148.5 |
| Exact cache | 0.05 | 0.05 | 148.6 |
| Semantic cache | 0.14 | 0.18 | 148.7 |
| Cheaper model | 31.8 | 41.2 | N/A |
| Small classifier | 0.18 | 0.22 | 148.7 |
| **Ink (JIT)** | **0.17** | **0.21** | 148.7 |

These figures are local Fast Path serve latency only. They are **not** whole-workflow
latency. Unqualified decisions pay full fallback latency. Learned local decisions (unseen
but similar states) run through the local model at roughly 48 ms, not sub-millisecond.

## 11. Cold start

| Arm | Calls to first saving | Calls to net break-even |
| :--- | :---: | :---: |
| Exact cache | 4 | 4 |
| Semantic cache | 1 | 1 |
| Cheaper model | 1 | 1 |
| Small classifier | 1 | 2 |
| **Ink** | 28 | 42 |

Ink intentionally pays a higher cold-start cost to require shadow verification before
serving.

## 12. Drift: wrong serves before revocation

Under injected passive policy drift (eval decisions 600–900):

- Exact cache: served stale decisions until evaluation end (no invalidation).
- Semantic cache: served stale decisions (matched obsolete prototypes).
- Small classifier: served stale decisions (obsolete weights).
- **Ink:** incurred **7–8 wrong serves** before automatic demotion, detected through
  comparison traffic and downstream verifier signals; the artifact was demoted to shadow
  and further traffic redirected to fallback, then requalified in recovery.

## 13. Revocation quality

- Total revocations observed: 3 (one per active workload during drift).
- False revocations observed: 0.
- Requalifications: 3 (all active workloads requalified during recovery).

## 14. Negative control (`research_novelty`)

Ink's profiler detected high entropy and lack of repeated clusters and refused
compilation. Calls avoided: 0%. Wrong serves: 0. A naive semantic cache at threshold 0.85
served these novel queries locally with a 2.56% wrong-serve rate.

## 15. Comparison caveats

- A cheaper-model tier still reasons on every request and carries its own baseline error
  rate; it does not remove inference.
- Semantic and exact caches can show higher raw call reduction only by ignoring verified
  correctness. This benchmark measures both together.
- All whole-application figures depend on the bounded-traffic share (15%–25% here).

## 16. Raw artifacts

- Decisions log: `benchmarks/results/competitive_frontier/decisions.jsonl`
- Summary metrics: `benchmarks/results/competitive_frontier/summary.json`
- Tabular data: `benchmarks/results/competitive_frontier/arms_summary.csv`
- Frontier charts: `frontier_primary_calls_vs_wrong_serves.svg`,
  `frontier_secondary_cost_vs_weighted_error.svg`, `frontier_latency_vs_error.svg`
- Runner: `benchmarks/competitive_frontier/run.py`; report generator:
  `benchmarks/competitive_frontier/report.py`
