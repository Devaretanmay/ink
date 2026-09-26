# Microloop Validation Benchmark v1 (Experiment 001)
## Experimental Protocol & Scientific Specification

**Tagline:** *Microloop — Keep agents making progress.*  
**Internal Description:** *Real-time trajectory failure detection and recovery for autonomous agents.*  

---

## 1. Objective & Scientific Hypothesis

The objective of **Experiment 001** is to test whether real-time trajectory monitoring and structured recovery feedback improve autonomous coding agent completion rates without altering the underlying model, system prompt, or tool harness.

### Core Hypotheses
* **$H_1$ (Primary Task Lift):**  
  Microloop increases task completion rate on SWE-bench Verified over a Vanilla baseline under an identical model and total resource budget.
* **$H_2$ (Outperforming Retry):**  
  Microloop achieves higher completion than a standard retry/restart baseline with equal token and time limits.
* **$H_3$ (Wasted Action Reduction):**  
  Microloop reduces wasted tool actions after the onset of failure by $\ge 15.0\%$.
* **$H_4$ (Low Disruption / Non-Damaging):**  
  Microloop's damaging intervention rate (intervening on a run that would otherwise have succeeded) is $< 5.0\%$.

---

## 2. Experimental Design Matrix

### 2.1 The 3 Evaluation Stages

```
   ┌────────────────────────────────────────────────────────┐
   │             STAGE 1: MICROLOOP DEV                     │
   │  30 tasks (SWE-bench Verified) × 3 seeds × 4 conditions│
   │  360 total runs per model                              │
   │  Purpose: Rapid detector tuning & parameter freezing   │
   └───────────────────────────┬────────────────────────────┘
                               │ Gates Passed: Positive causal signal
   ┌───────────────────────────▼────────────────────────────┐
   │             STAGE 2: MICROLOOP VALIDATION              │
   │  100 SWE-bench Verified + 25 Terminal-Bench 2 / Harbor │
   │  125 tasks × 3 seeds × 4 conditions × 2 models         │
   │  3,000 total runs                                      │
   │  Purpose: Disjoint, held-out validation of thesis       │
   └───────────────────────────┬────────────────────────────┘
                               │ Gates Passed: Generalization proven
   ┌───────────────────────────▼────────────────────────────┐
   │             STAGE 3: MICROLOOP BENCHMARK               │
   │  300–500 tasks (Full SWE-bench Verified)               │
   │  Purpose: Definitive, publishable YC benchmark results │
   └────────────────────────────────────────────────────────┘
```

### 2.2 Randomized Block Interleaving & Provenance

To eliminate confounding variables from model provider infrastructure drift:
1. **No Sequential Batching:** We do not run Vanilla on week 1 and Microloop on week 2.
2. **Randomized Block Schedule:** Conditions are interleaved task by task:
   $$\text{Task 001}: \text{Run 1 (Vanilla)} \to \text{Run 2 (Microloop)} \to \text{Run 3 (Retry)}$$
   $$\text{Task 002}: \text{Run 1 (Retry)} \to \text{Run 2 (Vanilla)} \to \text{Run 3 (Microloop)}$$
3. **No `latest` Aliases:** Pinned model version IDs, explicit temperature ($0.0$), reasoning parameters, and Git commit hashes are recorded for every trial.

### 2.3 Four Experimental Conditions

| Condition | Agent Harness | Model & Tools | Microloop | Intervention Policy |
|---|---|---|---|---|
| **A. Vanilla** | mini-SWE-agent v2 | Fixed pinned model & tools | None | None (standard execution until exit or budget cap) |
| **B. Retry Baseline** | mini-SWE-agent v2 | Fixed pinned model & tools | None | Retry tool once on error; restart agent once on failure with remaining budget |
| **C. LLM Supervisor** | mini-SWE-agent v2 | Fixed pinned model & tools | None | External LLM prompted every 5 steps to Continue/Replan/Restart/Stop |
| **D. Microloop** | mini-SWE-agent v2 | Fixed pinned model & tools | Attached | Deterministic trajectory monitoring + structured Replan |

---

## 3. Minimal Agent Harness: `mini-SWE-agent` v2

We wrap `mini-SWE-agent v2` inside a Docker/Podman sandbox:
* Very small control loop
* Linear conversation history
* Bash-based execution environment
* Multiple model providers supported
* Trajectory logging cleanly decoupled from orchestration

### Per-Run Budget Equivalence
All conditions operate under identical maximum limits:
* Maximum Steps: 50–100
* Maximum Wall-Clock Time: 30 minutes
* Maximum Tokens: 100,000 tokens

---

## 4. Pass 2.5: Failure Discovery & Taxonomy

Before building detectors 4, 6, 7, and 8, baseline trajectories are cataloged in `benchmarks/analysis/failure-taxonomy-v1.json`:
* **Successful-Efficient:** Direct resolution without stalling.
* **Successful-Wasteful:** Completed task but wasted $30+$ tool calls on unguided loops.
* **Failed-Recoverable:** Trajectory stalled on recurring error or stagnation where guidance could alter outcome.
* **Failed-Irrecoverable:** Fundamental reasoning failure or missing domain capability.

---

## 5. Statistical Methodology

1. **Primary Analysis:**  
   Task-level paired bootstrap confidence interval ($B = 10,000$ resamples) on completion-rate difference:
   $$\Delta \text{ACR} = \text{ACR}_{\text{Microloop}} - \text{ACR}_{\text{Baseline}}$$
2. **Secondary Analyses:**  
   * Wilson score intervals for condition success rates.
   * McNemar’s test for directly paired task outcomes ($p < 0.05$).
   * Relative tool-call and token spend reduction on failed/stalled trajectories.
   * Damaging intervention rate: Control succeeded while treatment failed after intervention.
