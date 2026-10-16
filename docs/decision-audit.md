# Decision audit

A Decision Audit is the structured evaluation process we use to figure out whether an existing production AI decision can safely become local software.

Here is the deal:

> **Give us one high-volume bounded model decision. We will determine whether any portion of it has a defensible path toward local serving authority.**

---

## Why audit before integrating?

Not every AI decision should become local software.

If a decision requires unbounded natural language generation, lacks ground-truth verification signals, or experiences continuous non-stationary drift, local compilation is a bad idea.

Ink conducts an audit to establish whether empirical evidence justifies local serving authority.

> [!IMPORTANT]
> **A negative result is a good outcome.**
> If an audit reveals that your workload can't satisfy statistical safety contracts or lacks reliable outcome verification, we will tell you not to compile it. We never weaken qualification thresholds or relax error budgets just to manufacture a positive pilot.

---

## The 15-point audit checklist

Every Decision Audit analyzes the target decision across fifteen specific dimensions:

| # | Dimension | Evaluation Criteria |
| :--- | :--- | :--- |
| **1** | **Decision Definition** | What exact business choice is being resolved? Is the boundary well-defined and isolated from open-ended generation? |
| **2** | **State Schema** | What inputs are provided to the model? Which features are exact structured keys vs. natural language strings? |
| **3** | **Finite Choice Set** | Is the set of valid outputs discrete and enumerable (e.g., tool names, triage queues, approval decisions)? |
| **4** | **Production Volume** | How many times per day/month is this decision executed? Is volume sufficient to accumulate meaningful evidence? |
| **5** | **Current Host Model** | Which model currently handles the decision (e.g., GPT-4o, Claude 3.5 Sonnet)? |
| **6** | **Current Cost** | What are the monthly token fees incurred solely for this bounded decision? |
| **7** | **Current Latency** | What is the p50, p90, and p99 network and inference latency experienced by the caller? |
| **8** | **Outcome Source** | Where does ground truth come from? Is outcome provenance high-integrity (deterministic test, API success, user confirmation) or weak (model judge, heuristic)? |
| **9** | **Risk & Error Budget** | What is the maximum tolerable error rate (e.g., $\le 1.0\%$, $\le 5.0\%$)? Can business operations withstand localized fallback? |
| **10** | **Repeatability** | How frequently do identical or semantically similar states recur in real traffic? |
| **11** | **Evidence Availability** | Are historical traces or outcome logs already stored, or must an observation window be initialized? |
| **12** | **Qualification Feasibility** | Given the error budget and volume, how many verified observations are required to clear the Wilson 95% confidence lower bound? |
| **13** | **Candidate Fast Path Coverage** | What percentage of total traffic falls into high-density, qualified execution regions? |
| **14** | **Expected Fallback Rate** | What proportion of traffic is genuinely novel and must continue routing to the Host model? |
| **15** | **Failure Modes & Resilience** | If an unseen state occurs or upstream schemas change, does the application safely fall open to the Host model without disruption? |

---

## The audit lifecycle

```text
Discovery & Trace Ingestion
          │
          ▼
Site Definition & Contract
          │
          ▼
Outcome Provenance Audit
          │
          ▼
Statistical Feasibility Check
          │
   ┌──────┴──────────────────────────┐
   │                                 │
   ▼                                 ▼
PASS (Defensible Path)             FAIL (Unsuitable)
- Candidate coverage identified    - Unverifiable outcomes
- Required evidence calculated     - Non-convergent distribution
- Shadow qualification begins      - Workload remains with Host
```

### Stage 1: Discovery (`ink discover`)
Using existing trace logs (OpenTelemetry, LangSmith, LiteLLM, or JSONL), Ink scans for repeated call clusters, extractable discrete outputs, and recurrent state keys:

```bash
ink discover traces.jsonl --profile
```

### Stage 2: Contract formalization
If candidate sites appear viable, an explicit `DecisionSite` contract is defined:

```python
from ink import DecisionSite

site = DecisionSite(
    name="order.routing",
    state_schema={"customer_tier": "string", "query": "string"},
    choices=("standard_queue", "priority_queue", "fraud_review"),
    local_error_budget=0.01,
)
```

### Stage 3: Outcome provenance verification
We examine how downstream systems report success:
- **Strong:** Deterministic API responses, explicit human agent confirmations, completed payments, regression test suites.
- **Weak:** Model-as-a-judge scores, speculative heuristics, self-reported agent assertions.

If the outcome source is weak, the audit focuses on instrumenting a reliable verifier before compiling candidates.

### Stage 4: Statistical safety simulation
Ink calculates the sample depth $n$ required to prove that the Wilson 95% confidence lower bound clears $1.0 - \text{local\_error\_budget}$:

$$\text{Wilson Lower Bound}(k, n) \ge 1.0 - \text{local\_error\_budget}$$

If the required sample volume cannot realistically be collected within the desired timeframe, the decision site remains in `OBSERVE` mode.

---

## Audit outcomes

An audit delivers one of three recommendations:

1. **Qualified for immediate shadowing:**
   The decision is bounded, outcomes are verifiable, and recurrence is high. Ink installs a DecisionSite in shadow mode to accumulate evidence.
2. **Instrument outcome feedback first:**
   The decision boundary is well-suited, but ground-truth outcome signals are currently missing or low-integrity. We define the verification signal before compiling.
3. **Reject for local compilation:**
   The workload is open-ended, lacks repeatable structure, or tolerates zero risk with insufficient volume. The workload remains permanently with the Host model.

---

## Run an audit today

To evaluate a production decision site, run `ink discover` on your existing traces:

```bash
ink discover traces.jsonl --profile
```
