# Microloop: Real-Time Trajectory Failure Detection and Recovery
## Product Requirements Document (PRD) — Version 0.3 / Experiment 001

**Status:** Approved Strategic Direction & Active Development  
**Tagline:** *Microloop — Keep agents making progress.*  
**Internal Description:** *Real-time trajectory failure detection and recovery for autonomous agents.*  
**Target Milestone:** Experiment 001 (Microloop Validation Benchmark v1)  
**Target Window:** Y Combinator Winter 2027 Cycle (Deadline: November 2, 2026)  

---

## 1. Executive Summary & Core Thesis

As autonomous AI agents evolve from short interactive chat loops into long-running workers executing for 30 minutes to 6 hours across terminal sessions, source trees, APIs, and cloud infrastructure, their primary failure mode changes. The fundamental bottleneck in production is **unverifiable progress and unrecoverable execution failure**.

When an agent hits an error today:
$$\text{Action Fails} \longrightarrow \text{Context Balloons} \longrightarrow \text{Model Confused} \longrightarrow \text{Oscillating Retries} \longrightarrow \text{Task Aborts}$$

**Microloop detects when autonomous agents stop making useful progress and helps them recover.**

Microloop sits beneath any agent harness, tracking execution trajectories locally, verifying objective environmental progress, and delivering structured recovery signals before an agent exhausts its step or token budget. Checkpointing and transactional rollbacks represent future expansion layers; the validated product center today is **real-time non-progress detection and recovery feedback**.

```
┌────────────────────────────────────────────────────────┐
│                      Agent Host                        │
│   (mini-SWE-agent v2 / Claude Code / OpenAI / Custom)  │
└───────────────────────────┬────────────────────────────┘
                            │ Action & Observation
┌───────────────────────────▼────────────────────────────┐
│                    MICROLOOP RUNTIME                   │
│  ┌──────────────────────────────────────────────────┐  │
│  │ Trajectory Engine (Fast Local Rust Core)         │  │
│  │  - Repetition & Masked Normalization (D1, D2)    │  │
│  │  - Error Recurrence Tracking (D3)                │  │
│  │  - State Stagnation Detection (D5)               │  │
│  │  - Progress Scoring & Heuristic Severity         │  │
│  ├──────────────────────────────────────────────────┤  │
│  │ Policy Engine                                    │  │
│  │  - Observe (Telemetry & Diagnostics)             │  │
│  │  - Replan (Structured Context Injection)         │  │
│  │  - Stop (Budget Governance)                      │  │
│  └──────────────────────────────────────────────────┘  │
└───────────────────────────┬────────────────────────────┘
                            │ Recovery Signal
┌───────────────────────────▼────────────────────────────┐
│                  Host Agent Continues                  │
│       (Informed Course-Correction, Zero Cloud Leak)    │
└────────────────────────────────────────────────────────┘
```

---

## 2. Immediate Milestone: Experiment 001 (Validation Benchmark v1)

We do not build the full company until empirical benchmarks prove Microloop changes outcomes.

### 2.1 Primary Hypothesis
* **H1:** Microloop increases end-to-end task completion rate by detecting non-progress earlier than the base agent and applying targeted recovery feedback without modifying the underlying model, system prompt, or tool set.

### 2.2 Secondary Hypotheses
* **H2 (Wasted Action Reduction):** Microloop significantly reduces wasted tool calls on failed or stalled trajectories.
* **H3 (Token Efficiency):** Microloop reduces tokens consumed after the trajectory has entered an unrecoverable failure state.
* **H4 (Outperforming Naive Retries):** Microloop recovers more failing runs than naive retry/restart policies at the same total compute budget.
* **H5 (Low Disruption / Non-Damaging):** Microloop does not interrupt healthy exploration often enough to offset the completion gains (false intervention rate $< 5\%$).
* **H6 (Generalization):** Microloop’s trajectory heuristics generalize across different LLM backends and across distinct task distributions (SWE-bench Verified and Terminal-Bench 2 / Harbor).

---

## 3. The Benchmark Strategy: 3-Stage Validation Ladder

Evaluation progresses across three strict stages:

| Stage | Benchmark Suite | Tasks | Trials / Cond. | Total Runs / Model | Purpose |
|---|---|---|---|---|---|
| **Stage 1: Microloop Dev** | SWE-bench Verified | 30 | 3 | 360 | Rapid detector tuning, calibration, and baseline failure analysis |
| **Stage 2: Microloop Validation** | SWE-bench Verified + Terminal-Bench 2 | 100 + 25 | 3 | 1,500 | Statistically powered comparison across two distinct models |
| **Stage 3: Microloop Benchmark** | Full SWE-bench Verified | 300–500 | 3 | 3,600–6,000 | Definitive publishable benchmark and YC batch evidence |

### 3.1 Task Freezing Without Cherry-Picking
* **Dev Manifest (`benchmarks/manifests/dev-v1.json`):** 30 tasks frozen before testing (10 easier, 10 medium, 10 harder).
* **Validation Manifest (`benchmarks/manifests/validation-v1.json`):** 100 disjoint tasks held out from tuning.
* **Integrity Rule:** Tasks are frozen independently of outcome. We do **not** select tasks to artificially hit a 50% baseline. The model/budget combination is calibrated to land naturally with sufficient headroom for recovery (roughly 20%–80% baseline completion, ensuring $\ge 20$ natural failures to study).

### 3.2 Randomized Block Interleaving & Pinned Model Versions
* **No Sequential Batching:** We do not run all Vanilla runs on day 1 and all Microloop runs on day 10. Provider infrastructure shifts over time.
* **Randomized Block Schedule:** Conditions are interleaved within each task using a randomized block design:
  $$\text{Task 001}: \text{Run 1 (Vanilla)} \to \text{Run 2 (Microloop)} \to \text{Run 3 (Retry)}$$
  $$\text{Task 002}: \text{Run 1 (Retry)} \to \text{Run 2 (Vanilla)} \to \text{Run 3 (Microloop)}$$
* **No `latest` Aliases:** Every run explicitly logs pinned model versions, reasoning effort, temperature, and commit hashes:
  ```json
  {
    "provider": "anthropic",
    "model": "claude-3-7-sonnet-20250219",
    "reasoning_effort": "medium",
    "temperature": 0.0,
    "experiment_commit": "5bff65b"
  }
  ```

### 3.3 Four Agent Experimental Conditions
1. **Condition A: Vanilla (Baseline)** — Minimal base agent. No Microloop, no retry.
2. **Condition B: Retry Baseline** — Agent + naive retry policy (retry failed tool once; restart agent once on terminal failure with remaining budget). Frozen before Microloop results.
3. **Condition C: LLM Supervisor** — An external supervisory prompt receives recent trajectory history every $N$ steps to decide: Continue, Replan, Restart, or Stop.
4. **Condition D: Microloop (Treatment)** — Identical base agent, identical model and prompt, with local Microloop trajectory monitoring and recovery feedback.

### 3.4 Budget Equivalence
All conditions operate under identical maximum resource constraints:
* Maximum steps: 50–100
* Maximum wall-clock time: 30 minutes
* Maximum token budget: 100,000 tokens

---

## 4. Trajectory Engine: Data-Driven Detector Strategy

Rather than building an 8-detector theoretical taxonomy before observing agent behavior, Microloop follows a strict **data-driven sequence**:

$$\text{Pristine Baseline Trajectories} \longrightarrow \text{Pass 2.5 Failure Discovery} \longrightarrow \text{Build Only Justified Detectors}$$

### 4.1 Phase 1 Core Detectors (Pass 3)
We start with four brutally simple signals:
1. **D1: Exact Repetition:** Identical tool calls and outputs recurring in the sliding window.
2. **D2: Normalized Repetition:** Repetition detected after masking noise (timestamps, UUIDs, PIDs, paths).
3. **D3: Error Recurrence:** Tracking recurring error signatures across non-consecutive steps.
4. **D5: State Stagnation:** Monitoring active tool execution while objective progress metrics (test failures) remain stagnant.

### 4.2 Derived Detectors (Pass 2.5 $\to$ Pass 3)
Detectors 4 (State Oscillation), 6 (Regression), 7 (Tool Thrashing), and 8 (Failure Cascade) are implemented **only if** real baseline trajectories in `benchmarks/analysis/failure-taxonomy-v1.json` demonstrate their empirical frequency.

---

## 5. Pass 2.5: Failure Discovery & Taxonomy

After running Vanilla baselines in Pass 2, trajectories are classified into four actionable buckets:
1. **Successful-Efficient:** Solved with minimal steps and zero unproductive loops.
2. **Successful-Wasteful:** Solved, but wasted 30+ tool calls spinning on intermediate errors. (High-value commercial optimization).
3. **Failed-Recoverable:** Stalled on fixable errors, oscillation, or stagnation. (Primary target for Microloop lift).
4. **Failed-Irrecoverable:** Fundamental reasoning failure or missing domain capability.

---

## 6. Offline Trajectory Replay Tooling

To allow rapid detector development without burning model API budgets:
* Raw trajectories are stored separately from derived features:
  ```text
  results/run_abc/
  ├── metadata.json           # Exact provenance, model ID, seeds, budget
  ├── trajectory.jsonl        # Raw agent steps (commands, stdout, exit codes)
  ├── final.patch             # Extracted diff
  ├── evaluation.json         # Official ground-truth evaluation
  └── microloop_features.jsonl# Derived detector decisions and evidence
  ```
* **Offline Replay CLI:** Developers can replay raw trajectories through the Rust engine:
  ```sh
  microloop replay results/run_abc/trajectory.jsonl
  ```
  Enables continuous calibration of detector precision and recall with zero API costs.

---

## 7. Statistical Evaluation Methodology

1. **Primary Analysis:** Task-level paired bootstrap confidence interval ($B = 10,000$ iterations) on completion-rate difference ($\Delta \text{ACR}$).
2. **Secondary Analyses:**
   * Wilson score confidence intervals for individual condition completion rates.
   * McNemar’s test for directly paired task outcomes ($p < 0.05$).
   * Relative tool-call and token spend reduction on failed/stalled trajectories.
   * Damaging intervention proxy: Control succeeded while treatment failed after intervention.

---

## 8. Updated Master Roadmap

```
PASS 1: Architecture + Benchmark Scaffold [COMPLETE]
  │
  ▼
PASS 2: Harness + Telemetry + Baseline Trajectories
  │  ├── mini-SWE-agent v2 integration + Docker sandbox
  │  ├── Trajectory adapter & canonical JSONL telemetry
  │  ├── Offline replay tool (microloop replay)
  │  ├── Smoke test (3 runs) → Calibration (10 runs) → 90 Vanilla runs
  │  └── Frozen Retry baseline (90 runs)
  │
  ▼
PASS 2.5: Failure Discovery & Taxonomy
  │  ├── Analyze real failure trajectories (failure-taxonomy-v1.json)
  │  └── Characterize: successful-wasteful vs failed-recoverable
  │
  ▼
PASS 3: Build Only Justified Detectors
  │  ├── D1, D2, D3, D5 in Rust core
  │  └── Add only detectors validated by Pass 2.5 data
  │
  ▼
PASS 4: Recovery Intervention + Experiment 001
  │  ├── Structured recovery signal injection + cooldown
  │  └── 360 dev runs (Vanilla vs Retry vs Supervisor vs Microloop)
  │
  ▼
PASS 5: Fault Injection Suite + LLM Supervisor
  │  ├── 50 deterministic fault scenarios
  │  └── Precision / recall / latency curves
  │
  ▼
PASS 6: Held-Out Validation & Second Model
  │  ├── 100 SWE-bench Verified tasks + Terminal-Bench 2 / Harbor
  │  ├── Second model evaluation (cross-model stability)
  │  └── Bootstrap confidence intervals (Target: ≥ 8 pp lift on held-out)
  │
  ▼
PASS 7: Killer Demo & YC W27 Application Package
```
