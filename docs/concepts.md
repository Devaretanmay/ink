# Ink Concepts & Mental Model

This document outlines the core concepts, entities, and operational mental models of the Ink Decision JIT.

---

## 1. Core Primitives

### 1.1 DecisionSite
A **DecisionSite** represents a named, bounded choice within an AI agent's execution graph. It defines:
- `name`: Unique hierarchical identifier (e.g. `support.route`, `agent.tool_select`).
- `state_schema`: Strongly typed dictionary of expected input state variables. Supported types are `string`, `integer`, `number`, `boolean`, and their nullable variants (e.g. `string?`).
- `choices`: Tuple of discrete, declared string choices (at least two choices).
- `fallback_revision`: Version string representing the prompt, model, or business policy. Any change to prompt instructions should increment this revision to trigger safe requalification.
- `fallback_model_calls`: Optional fixed count of LLM invocations incurred by the fallback (e.g. `1`).

```python
from ink import DecisionSite

site = DecisionSite(
    name="support.route",
    state_schema={"text": "string", "amount": "integer"},
    choices=("refund", "request_info", "specialist"),
    fallback_revision="1",
)
```

### 1.2 DecisionResult
The return value of `client.decide()`. It is an immutable dataclass containing:
- `choice`: Selected action string (either from fast path or fallback).
- `decision_id`: Unique 32-character hexadecimal identifier.
- `source`: Strictly `"fast_path"` or `"fallback"`.
- `site_version`: Hash of the site contract.
- `fast_path_version`: Hash of the serving artifact (if served locally).
- `fallback_reason`: Reason for fallback invocation (`"cold_start"`, `"outside_coverage"`, `"insufficient_confidence"`, `"comparison"`, or `"storage_unavailable"`).
- `confidence`: Calibrated empirical quality lower bound.
- `recorded`: Boolean flag indicating whether decision history was committed to SQLite.
- `receipt`: Cryptographic receipt verifying execution origin and safety margins.

### 1.3 FallbackResult
When an agent's fallback executes, it can return either a plain choice string or a structured `FallbackResult`. Returning a `FallbackResult` allows Ink to record exact model calls, token counts, and cloud costs:

```python
from ink import FallbackResult

def my_fallback():
    response = call_cloud_llm(...)
    return FallbackResult(
        choice="refund",
        model_calls=1,
        input_tokens=142,
        output_tokens=6,
        cost=0.0018,
    )
```

### 1.4 Outcome & Independent Verifiers
An **Outcome** represents an immutable verification signal generated after action execution:
- `quality`: Numerical score in $[0.0, 1.0]$ ($1.0 = \text{success}$, $0.0 = \text{failure}$).
- `verifier`: Name of the independent verification subsystem.
- `verifier_version`: Version string of the verification rules.
- `evidence`: Arbitrary JSON dictionary of factual receipts.

> [!IMPORTANT]
> **Independent Verification Rule:** A verifier must never ask an LLM *"Did you do a good job?"* (circular reasoning). A verifier must check objective reality: did the database update succeed? Did the API return 200 OK? Did the user cancel or confirm?

---

## 2. The Decision JIT Lifecycle

```text
  [ OBSERVE ] ──(State Repeat >= 50% & Positive ROI)──▶ [ CANDIDATE ]
                                                              │
                                                      Compile Local Path
                                                              │
                                                              ▼
  [ ACTIVE ] ◀──(Hoeffding Holdout Bound >= Policy)─── [ SHADOW ]
      │                                                       ▲
      │                                                       │
      └────── Policy Drift Detected / Accuracy Drop ──────────┘
             (Autonomous Demotion in 1 - 4 Decisions)
```

### State Machine Definitions:
- **`OBSERVE`**: Cold-start state. All traffic invokes the remote LLM fallback. Decisions and outcomes are logged to local SQLite storage.
- **`CANDIDATE`**: A local fast path has been fitted by `client.compile()`, producing an immutable artifact payload.
- **`SHADOW`**: The artifact is evaluated against live incoming traffic in shadow mode. Incoming requests invoke the fallback to control the agent, while the shadow candidate predicts in the background.
- **`VERIFIED` / `ACTIVE`**: Once shadow predictions pass the site's required quality threshold under Hoeffding bounds on fresh holdout outcomes, the artifact is promoted to `ACTIVE` and serves live requests in $<0.5$ ms.
- **`RETIRED`**: When an artifact is superseded or permanently demoted, it is retired with an immutable lineage link.

---

## 3. Economic Profiling & Advisory Recommendations

Not every decision site is worth compiling. `client.profile(site)` produces a `SiteProfile`:
- **`unique_state_ratio`**: Fraction of observed decisions that were unique.
- **`exact_repeat_rate`**: $1.0 - \text{unique\_state\_ratio}$.
- **`choice_entropy`**: Shannon entropy of output distribution in bits ($-\sum p_i \log_2 p_i$).
- **`outcome_completeness`**: Fraction of decisions with recorded outcomes.
- **`policy_volatility`**: Rate of conflicting choices on identical states over time.
- **`break_even_decisions`**: Estimated calls until qualification costs are amortized.

### Advisory Categories:
- **`strong_candidate`**: High repetition ($\ge 50\%$), high outcome completeness ($\ge 80\%$), bounded volatility ($\le 15\%$). Recommended for shadow compilation.
- **`needs_more_data`**: Fewer than 50 observations accumulated.
- **`poor_repetition`**: High entropy / unique state ratio $> 85\%$. Fast paths will rarely trigger.
- **`weak_verifier`**: Outcome completeness $< 80\%$. Cannot achieve shadow promotion.
- **`high_volatility`**: Policy changes $> 15\%$. Frequent drift causes constant demotions.
- **`low_economic_value`**: Fallback latency $< 10$ ms and cost $< \$0.0001$.

---

## 4. When to Use (and When NOT to Use)

| Dimension | Ideal for Ink | DO NOT Use Ink |
| :--- | :--- | :--- |
| **Output Type** | Discrete choices, tools, actions, routes | Open-ended text, dialogue, creative prose |
| **Output Space** | Bounded ($\le 20$ discrete actions) | Unbounded / high entropy ($> 4.5$ bits) |
| **Traffic Profile**| Repetitive / Zipfian skew ($\ge 50\%$ repeats) | Diffuse / unique (random web browsing) |
| **Verification** | Factual downstream receipts (API, DB, DOM) | Subjective or unmeasurable outcomes |
| **Policy Lifespan**| Stable policies (drift interval $\ge 20k$ calls) | Hyper-volatile (prompt changes every 50 calls) |
