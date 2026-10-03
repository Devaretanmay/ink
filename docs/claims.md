# Microloop Claims Registry

Every external claim about Microloop is classified into one of four tiers.

| Category | Definition |
| :--- | :--- |
| **MEASURED** | Empirically verified on defined benchmark workloads with reproducible seeds. |
| **CONDITIONAL** | True under stated conditions; depends on workload characteristics. |
| **VISION** | Future direction; not implemented or verified. |
| **NOT SUPPORTED** | Claim that should not be made. |

---

## Claims

### Local Fast-Path Latency
- **Category:** `MEASURED`
- **Statement:** Qualified local decisions executed in 0.18–0.19 ms (p50) in the competitive benchmark.
- **Evidence:** `benchmarks/results/competitive_frontier/summary.json` — 18,000 decisions across 4 workloads, strict 70/30 temporal evaluation.
- **Scope:** Applies only to decisions served from a promoted, verified fast path. Fallback decisions still incur standard remote model latency (120–250 ms typical).

---

### Drift Detection and Demotion
- **Category:** `MEASURED`
- **Statement:** On tested policy-drift workloads, Microloop demoted stale behavior after 7–8 incorrect local serves, while static cache baselines continued producing 12.0–18.0% stale wrong-serve rates under the same conditions.
- **Evidence:** `benchmarks/results/competitive_frontier/REPORT.md` — passive drift injection with no explicit signal.
- **Scope:** Requires active comparison traffic (default 5–35% during active serving). Drift detection speed depends on comparison rate and traffic volume.

---

### Whole-Application Call Reduction
- **Category:** `CONDITIONAL`
- **Statement:** Whole-application savings depend on what percentage of an application's traffic consists of bounded, repetitive, independently verifiable decisions. In tested workloads, bounded sites represented 15–25% of total application calls, yielding 3.99–10.10% whole-application call reduction.
- **Evidence:** `benchmarks/results/competitive_frontier/summary.json`.
- **Conditions:** Applications with higher bounded traffic share will see proportionally higher savings. Applications with mostly open-ended generation will see near-zero savings.

---

### Whole-Application Spend Reduction
- **Category:** `CONDITIONAL`
- **Statement:** 3.51–8.87% whole-application spend reduction was measured across tested workloads.
- **Evidence:** `benchmarks/results/competitive_frontier/summary.json`.
- **Conditions:** Same as call reduction — depends on bounded traffic share and per-call cost.

---

### High-Entropy Workload Rejection
- **Category:** `MEASURED`
- **Statement:** The profiler correctly identified a high-entropy, non-repetitive research workload and refused compilation, producing zero wasted qualification resources. Semantic caching under the same conditions asserted false matches at a 2.56% error rate.
- **Evidence:** `benchmarks/results/competitive_frontier/REPORT.md` — `research_novelty` workload (3,000 decisions, unique UUID session tokens, zero repetition).

---

### Exact-Match Behavioral Consistency
- **Category:** `MEASURED`
- **Statement:** When serving from a promoted exact-state fast path, the served choice matches the verified training decision for that exact canonical state key deterministically.
- **Evidence:** `tests/test_safety_invariants.py`, `tests/test_decision_jit.py`.
- **Scope:** Valid for exact canonical JSON keys. Does not protect against stale policy if comparison traffic is disabled.

---

### Monotonic Promotion Safety
- **Category:** `MEASURED`
- **Statement:** No fast path is served to live traffic without first passing shadow qualification with Hoeffding lower confidence bound exceeding the site's required quality threshold.
- **Evidence:** `decision_api.py::promote()`, `tests/test_safety_invariants.py`.
- **Scope:** Unconditional across all runtime engines.

---

### Selective Compilation
- **Category:** `MEASURED`
- **Statement:** Profiling sites before compilation and compiling only high-repetition candidates yields positive net economic ROI. Compiling unrepeatable or high-entropy sites produces negative return due to unamortized qualification costs.
- **Evidence:** `benchmarks/results/competitive_frontier/REPORT.md`, `tests/test_profiler_economics.py`.

---

### DecisionSite-Level Call Reduction
- **Category:** `CONDITIONAL`
- **Statement:** Within bounded decision sites, Microloop avoided 19.9–40.4% of site calls in tested workloads during steady-state active serving.
- **Evidence:** `benchmarks/results/competitive_frontier/summary.json`.
- **Conditions:** Depends on repetition rate, comparison traffic overhead, and qualification cost amortization. Higher repetition yields higher reduction.

---

### Internal Learned Decision Model
- **Category:** `MEASURED`
- **Statement:** Microloop includes its own learned decision model capability as part of the Decision Engine. Internal model weights are fine-tuned specifically for bounded categorical decision tasks (`microloop-finetune-v1`).
- **Evidence:** `python/microloop/microloop/internal/model/training.py`, `tests/test_model_training.py`, `tests/test_architecture_invariants.py`.
- **Scope:** Owned architecture consists of ModernBERT-large backbone with custom DecisionHead and Scorer.

---

### Candidate Proposes, Qualification Authorizes (Candidate != Authority)
- **Category:** `MEASURED`
- **Statement:** An internal learned model prediction carries zero serving authority on its own. Serving authority is granted exclusively by independent outcome qualification, coverage boundaries, and active status.
- **Evidence:** `python/microloop/microloop/decision_api.py::_route()`, `tests/test_architecture_invariants.py`.
- **Scope:** Unconditional across all candidate engines.

---

### Exact Execution Tier Optimization
- **Category:** `MEASURED`
- **Statement:** Exact repeated states with empirical qualification evidence execute directly via the `ExactEngine` tier in < 0.2ms, bypassing full neural inference.
- **Evidence:** `tests/test_architecture_invariants.py::test_exact_tier_bypasses_neural_inference`.
- **Scope:** Valid for proven exact states within an active qualified artifact.

---

### Host Model Fallback for Novelty
- **Category:** `MEASURED`
- **Statement:** Novel, unsupported, or uncertain states strictly route to the host application's original model fallback callable. Microloop never serves outside qualified coverage.
- **Evidence:** `tests/test_architecture_invariants.py::test_novel_state_outside_coverage_routes_to_host`.
- **Scope:** Always enforced; fail-open guarantee routes to host fallback under any runtime exception.

---

### Long Paths
- **Category:** `VISION`
- **Statement:** Long-term, Microloop may compile longer verified sequences of agent behavior (multi-step trajectories) into local procedures with checkpoints and deoptimization.
- **Scope:** Not implemented. Will only be built if 3+ independent partners demonstrate that individual DecisionSites are useful but most remaining cost lies in repeated multi-step trajectories.

---

## Claims That Must NOT Be Made

| Claim | Status | Why |
| :--- | :--- | :--- |
| "Microloop eliminates 80% of enterprise AI spend" | `NOT SUPPORTED` | Bounded sites are 15–25% of tested traffic. Whole-app savings were 3.5–8.9%. |
| "Zero errors" | `NOT SUPPORTED` | Microloop incurred 7–8 wrong serves before detecting drift. |
| "100% accuracy" | `NOT SUPPORTED` | 0 false serves is an empirical sample count on stationary synthetic benchmark traffic, not an absolute population guarantee. |
| "Bounded false serves" | `NOT SUPPORTED` | False serves accumulate under drift until comparison evidence triggers deoptimization. |
| "800x faster applications" | `NOT SUPPORTED` | 0.18ms applies to local fast-path serves only, not entire application workflows. |
| "Guaranteed safe" | `NOT SUPPORTED` | Safety depends on comparison traffic, verifier quality, and traffic volume. |
| "Missing outcomes are safe or negative" | `NOT SUPPORTED` | Unknown evidence remains unknown; missing evidence halts qualification rather than being assumed safe or failing. |
| "Replaces all model calls" | `NOT SUPPORTED` | Only bounded, repeating, verifiable decisions qualify. |
| "AI cache" or "semantic cache" | `NOT SUPPORTED` | Microloop uses outcome-verified qualification, not similarity-based caching. |
| "Zero network activity under all conditions" | `NOT SUPPORTED` | When the default learned model is enabled, weights (~807 MB) are downloaded from HuggingFace on first learned use if not cached. Microloop sends zero decision telemetry off the machine. |


---

## Public Communication Rules

1. **Always state the denominator.** "19.9% of DecisionSite calls" is not "19.9% of all calls."
2. **Always report verified errors alongside call reduction.** Never quote savings without stating the error rate.
3. **Always report qualification overhead.** Net savings must account for observation and shadow costs.
4. **Separate local fast-path latency from total workflow latency.**
5. **Use "approximately" for measured ranges.** The exact number depends on workload characteristics.
6. **Be explicit about network boundaries.** Microloop sends zero decision or outcome telemetry off the machine; initial model provisioning downloads public weights from HuggingFace unless pre-installed or disabled.
