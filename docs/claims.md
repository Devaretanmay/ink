# Microloop Claims Registry

This document establishes the empirical and mathematical claims made by Microloop. Every claim is categorized strictly into one of four tiers: **PROVEN**, **MEASURED**, **EXTRAPOLATED**, or **HYPOTHESIS**.

Universal claims without qualification are prohibited in all documentation, READMEs, and presentations.

---

## 1. Summary of Claim Categories

| Category | Definition | Standard of Proof |
| :--- | :--- | :--- |
| **PROVEN** | Mathematically or logically guaranteed invariants. | Formal proof, SQLite constraints, or deterministic invariants verified in test suite. |
| **MEASURED** | Empirically verified numbers on defined benchmark workloads. | End-to-end benchmark results with reproducible seeds and logged artifacts. |
| **EXTRAPOLATED** | Projections derived from validated models under specified assumptions. | Long-horizon simulations with stated distributions and sensitivity boundaries. |
| **HYPOTHESIS** | Theoretical conjectures or open research questions. | Plausible rationale awaiting empirical confirmation. |

---

## 2. Claims Registry

### Claim 1: Exact-Match Fast Paths Preserve 100% Behavioral Consistency
- **Category:** `PROVEN`
- **Exact Statement:** When serving from a promoted exact-state fast path under an active artifact, the served choice matches the verified training decision for that exact canonical state key with 100% deterministic fidelity.
- **Evidence:** `tests/test_safety_invariants.py`, `tests/test_decision_jit.py`.
- **Scope & Conditions:** Valid for exact canonical JSON keys. If the underlying policy changes without updating the site revision, comparison traffic will detect drift and demote the artifact.
- **Counter-examples / Failure Modes:** Does not protect against stale policy if comparison traffic rate is set to 0.

---

### Claim 2: Monotonic Promotion Safety Gate
- **Category:** `PROVEN`
- **Exact Statement:** No fast path is ever served to live traffic without first passing shadow qualification with Hoeffding lower confidence bound exceeding the site's required quality threshold.
- **Evidence:** `decision_api.py::promote()`, `tests/test_safety_invariants.py`.
- **Scope & Conditions:** Holds unconditionally across all runtime engines (`decision`, `exact`). The site profiler cannot bypass or grant serving authority.
- **Counter-examples / Failure Modes:** None.

---

### Claim 3: Real Cloud LLM Latency Reduction
- **Category:** `MEASURED`
- **Exact Statement:** On live cloud LLM workloads (Groq `qwen/qwen3.8-27b`), Microloop Decision JIT reduces decision latency from ~126ms (teacher p50) to 0.409ms (Microloop p50), achieving a 308x latency reduction.
- **Evidence:** `benchmarks/results/real_world_validation_v2.json` (`support_ticket_routing`).
- **Scope & Conditions:** Applies to calls served locally from promoted verified fast paths. Fallback calls still incur standard remote network latency.

---

### Claim 4: Drift Detection and Demotion Protects Against Stale Serves
- **Category:** `MEASURED`
- **Exact Statement:** When an upstream business policy drifts, Microloop detects the divergence via comparison traffic and autonomously demotes the artifact back to shadow within 1–4 mismatch observations, yielding a 99.6% reduction in false serves compared to naive semantic caching (4 false serves vs 1,026 false serves across 2,100 decisions).
- **Evidence:** `benchmarks/results/real_world_validation_v2.json`.
- **Scope & Conditions:** Requires non-zero comparison traffic (default 5–10% during active serving).

---

### Claim 5: Selective Compilation Outperforms Blind Compilation
- **Category:** `MEASURED`
- **Exact Statement:** Profiling decision sites prior to compilation and compiling only high-repetition candidate sites yields positive net economic ROI, whereas compiling unrepeatable or high-entropy sites produces negative economic return due to unamortized qualification costs.
- **Evidence:** `benchmarks/results/agent_site_selection.json`, `tests/test_profiler_economics.py`.
- **Scope & Conditions:** Valid across multi-step agent architectures (`agent.intent`, `agent.tool`, `agent.cont`).

---

### Claim 6: 75–85% Model-Call Reduction in Steady State
- **Category:** `EXTRAPOLATED`
- **Exact Statement:** On repetitive classification and routing workloads under Zipfian traffic ($s \ge 1.0$) with policy drift intervals $\ge 20,000$ decisions, Microloop achieves 75–85% model-call reduction in steady state (78.69% at 100k decisions, 91.07% at 1M decisions).
- **Evidence:** `benchmarks/results/long_horizon_economics.json`.
- **Scope & Conditions of Validity:**
  - Repetition rate $\ge 75\%$
  - Drift interval $\ge 20,000$ decisions
  - Comparison traffic rate $\le 5\%$
- **Known Failure Modes / When Claim Does NOT Hold:**
  - On exploratory, free-form, or uniform unique inputs (repetition $< 50\%$), call reduction drops to near 0%.
  - Under hyper-volatile policy drift (drift every $< 5,000$ decisions), qualification costs exceed avoided call savings.

---

### Claim 7: Sparse TF-IDF Dominance Over Heavy Dense Embeddings
- **Category:** `MEASURED`
- **Exact Statement:** Sparse n-gram TF-IDF representations deliver 50.0% semantic coverage recall at 0.012ms representation latency and 0.5MB memory footprint with 0.00% false serves, vastly outperforming dense ModernBERT representations (14.0% recall, 26.27ms, 918.5MB) on bounded agent decisions.
- **Evidence:** `benchmarks/results/contrastive_evaluation.json` (Phase 4).
- **Scope & Conditions:** Bounded short decision states (tickets, intents, parameters).

---

### Claim 8: Learned Dense Projections Outperform Sparse Representations on Novel Vocabulary
- **Category:** `HYPOTHESIS`
- **Exact Statement:** A contrastive dense projection layer trained on large-scale domain-specific out-of-vocabulary synonyms may achieve higher coverage recall than sparse TF-IDF on unstructured natural language states with zero lexical overlap.
- **Evidence:** Unverified. Currently rejected by Phase 4 benchmarks for production use. Retained as an open hypothesis for future evaluation.

---

### Claim 9: Zero-Touch Telemetry Discovery Precision
- **Category:** `MEASURED`
- **Exact Statement:** Microloop discovery achieved 100% precision and 100% recall on our initial 10-callsite synthetic discovery benchmark. In external pilot telemetry across 12 candidate callsites (Pilots A, B, and C), discovery correctly recommended 5 bounded callsites, rejected 3 for high entropy/unbounded text generation, rejected 3 for sub-threshold volume (<20 traces), and rejected 1 for low repetition (<15%), yielding 100% recommendation acceptance rate on viable candidates with 0% false recommendation rate.
- **Evidence:** `benchmarks/results/false_discovery_report.json`, `pilots/results/pilot_evaluation_summary.json`.
- **Scope & Conditions:** Valid for JSONL, OpenTelemetry, LangSmith, and LiteLLM trace streams. Free-form text synthesis and low-volume sites are explicitly marked non-compilable.

---

### Claim 10: External Pilot Real-World Fast-Path Execution
- **Category:** `REAL PILOT`
- **Exact Statement:** Across three independent external application pilot integrations (Agent Orchestration, Support Workflow Routing, and Autonomous Coding CI Agent), Microloop achieved 38.8% to 49.2% net model-call reduction, reduced decision latency from 250–1,200 ms down to 1.0–1.2 ms (up to 99.9% reduction), incurred 18–20 integration LOC with zero external framework dependencies, and maintained 0 false serves under production qualification invariants.
- **Evidence:** `pilots/results/pilot_evaluation_summary.json` (Phase 10).
- **Scope & Conditions:**
  - Evaluated on realistic production-like workloads with delayed downstream factual verifiers (tool exit codes, customer ticket resolution, pytest exit codes).
  - Explicit policy invalidation (`client.invalidate`) instantaneously steps down active artifacts to shadow without serving corrupted decisions.

---

## 3. Mandatory Public Communication Guidelines

1. **Recommended Pitch Statements:**
   - *"Microloop reduced false serves by 99.6% versus naive semantic caching in a 2,100-decision policy-drift benchmark."*
   - *"In external pilots across agent orchestration, support routing, and coding agents, Microloop avoided 38.8%–49.2% of model calls with 1.0–1.2ms local latency and 0 false serves."*
   - *"On our initial 10-callsite discovery benchmark, discovery achieved 100% precision and recall; in external pilot telemetry, it rejected all freeform text generation and low-volume callsites."*
2. **Never make universal claims:** Do not claim *"Microloop cuts LLM costs by 80%"*. State: *"Microloop cuts LLM calls by 75–85% on repetitive bounded decisions in steady state; on exploratory workloads, reduction is near 0%."*
3. **Always report false serves alongside call reduction:** Any report of avoided calls must state the verified false-serve rate (0 false serves observed across all three Phase 10 pilots).
4. **Always report qualification overhead:** Net savings must account for observation and shadow qualification sample requirements.
