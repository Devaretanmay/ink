# Core concepts

Ink is a **Behavior JIT** (Just-In-Time compiler). It turns repeated, verified AI decisions into local software.

Here are the core primitives and how they fit together.

---

## 1. DecisionSite

A **DecisionSite** is a bounded contract in your application code. It specifies a single decision point where an AI system selects one choice from a fixed, discrete set.

Every DecisionSite declares:
- `name`: Unique hierarchical identifier for the decision boundary (e.g. `agent.tool_choice`, `support.triage`).
- `choices`: Tuple of valid target choices.
- `state_schema`: Dictionary defining expected input field types (`string`, `integer`, `number`, `boolean`).
- `local_error_budget`: Maximum error rate you're willing to tolerate (e.g. `0.01` for 1%).

```python
from ink import DecisionSite

site = DecisionSite(
    name="agent.tool_choice",
    choices=("search_kb", "query_database", "escalate_human"),
    state_schema={"task_type": "string", "retries": "integer"},
    local_error_budget=0.01,
)
```

### The boundary principle
Ink compiles only bounded choices. Open-ended generation (such as writing prose, synthesizing arbitrary code, or open conversational chat) belongs to your Host model.

---

## 2. Host

The **Host** is your existing general-purpose model or agent (e.g. Claude, GPT-4o, Gemini).

When an incoming request is novel, unverified, ambiguous, or out-of-distribution, Ink routes immediately to the Host. The Host always handles novelty.

Ink never attempts to replace the Host on unseen inputs. Falling back to the Host is not a failure; it is Ink's primary safety invariant.

---

## 3. Verified outcomes & outcome provenance

A **verified outcome** is real-world feedback recorded after a downstream action completes.

Ink does not trust model self-evaluations or unverified assumptions. Authority requires empirical evidence:

```python
ink.record_outcome(
    decision_id=result.decision_id,
    quality=1.0,  # 1.0 = verified success, 0.0 = verified error/failure
    verifier="postgres_execution_signal",
    verifier_version="v1",
)
```

### Not all feedback is equal
A statistically rigorous qualification process built on a flawed verifier is still bad evidence. We grade feedback sources by integrity:

| Tier | Outcome Source | Description | Integrity Level |
| :--- | :--- | :--- | :--- |
| **High** | **Deterministic tests** | Code executes cleanly, assertions pass, compiler succeeds. | Highest |
| **High** | **Direct telemetry** | Downstream API returns 200 OK, database commit succeeds. | Highest |
| **High** | **Confirmed business events** | Customer completes checkout, invoice pays, disputed charge settles. | High |
| **High** | **Human confirmations** | Expert reviewer or human agent explicitly approves or corrects choice. | High |
| **Contextual** | **Automated heuristics** | Regex pattern matching or keyword presence in downstream output. | Medium |
| **Weak** | **Model judges (LLM-as-a-judge)** | A secondary LLM grades the output; prone to hallucination and circular bias. | Low |
| **Weak** | **Self-reported success** | The agent claims its own task succeeded without external validation. | Unacceptable for authority |

If your verification signal is noisy or biased, Ink's statistical tests will faithfully measure noisy evidence. A DecisionSite must be paired with high-integrity verifiers before local serving authority is granted.

---

## 4. The JIT lifecycle

Ink operates as a closed-loop lifecycle:

```text
       ┌───────────┐
       │  OBSERVE  │  Traffic routes 100% to Host; Ink logs states & verified outcomes
       └─────┬─────┘
             │ Minimum evidence threshold reached
             ▼
       ┌───────────┐
       │  QUALIFY  │  Independent statistical gate tests candidate against error budget
       └─────┬─────┘
             │ Wilson 95% LB >= 1.0 - error_budget
             ▼
       ┌───────────┐
       │  COMPILE  │  Generates local execution artifact (Exact or Linear)
       └─────┬─────┘
             │
             ▼
       ┌───────────┐
       │   SERVE   │  Qualified states served locally (< 0.1ms); novel states fall back
       └─────┬─────┘
             │
             ▼
       ┌───────────┐
       │  VERIFY   │  Continuous comparison sampling monitors drift; demotes on error
       └───────────┘
```

1. **Observe:** Your application runs normally. All requests execute via your Host model. Ink records states, choices, and verified outcomes in local SQLite storage.
2. **Compile:** Ink trains local execution artifacts (an `Exact` fingerprint hash table or a `Linear` decision boundary).
3. **Qualify:** Ink evaluates candidate performance on independent holdout data. A candidate must satisfy the site's required error budget under a **95% Wilson confidence lower bound**.
4. **Serve:** The qualified artifact earns serving authority. Matching production requests execute locally in sub-milliseconds without network or token costs.
5. **Verify:** Ink samples live traffic to audit active behavior against your Host model. When drift reevaluation detects that error rates exceed the budget, Ink revokes serving authority back to your Host model.

---

## 5. Canonical lifecycle state machines

Ink distinguishes four separate lifecycle state machines:

| Scope | States | Definition & Transition Rules |
| :--- | :--- | :--- |
| **DecisionSite (Operational Status)** | `OBSERVE` | Initial state. Site observes traffic; 100% of calls fall back to Host and record evidence. |
| | `SHADOW` | Candidate compiled and calibrated. Evaluates shadow traffic against Host outcomes. |
| | `ACTIVE` | Candidate qualified and granted serving authority. Qualified requests resolve on local Fast Path. |
| **Artifact (Durable Database Entity)** | `CANDIDATE` | Newly compiled artifact before evaluation calibration. |
| | `SHADOW` | Calibrated candidate collecting fresh evaluation and shadow traffic. |
| | `VERIFIED` | Passed statistical holdout and shadow evaluation gates. |
| | `ACTIVE` | Granted live execution authority (at most one active artifact per serving engine). |
| | `RETIRED` | Superseded by a newer artifact revision or explicitly invalidated. |
| **Semantic Region (`CoverageEngine`)** | `CANDIDATE` / `SHADOW` | Proposed semantic cluster undergoing shadow evidence accumulation. |
| | `ACTIVE` | Cleared minimum sample count and purity requirements; permitted to serve. |
| | `DEGRADED` | Cluster margin contracted due to observed counterexamples. |
| | `REVOKED` | Region revoked due to localized error degradation; matching inputs route to Host. |
| **Policy Representation State** | `UNKNOWN` | Initial state prior to representation utility evaluation. |
| | `EVALUATING` | Background comparison actively measuring neural representation contribution against classical baselines. |
| | `ENABLED` | Neural representations active in background compiler feature competition. |
| | `DISABLED` | Neural model disabled (via `INK_POLICY_MODEL=disabled` or platform limits). |
| | `DEGRADED` | Neural representation utility degraded or insufficient advantage; compiler prioritizes classical features. |

---

## 6. Serving authority

**Serving authority** is the permission granted to a local artifact to resolve live production traffic.

- **Prediction is not permission.** A model predicting high confidence does not have authority to serve.
- **Similarity is not permission.** Vector proximity to a past query does not grant authority.
- **Model identity is not permission.** Using a fancy neural model does not grant authority.

Serving authority belongs solely to statistical evidence.

---

## 7. Fast Paths

A **Fast Path** is a compiled, local execution engine that resolves decisions in memory without remote API calls.

Ink supports two local serving engines:
1. **Exact (`ExactEngine`):** Sub-microsecond hash table match on verified state fingerprints (typically $< 0.01\text{ ms}$ on local benchmarks).
2. **Linear (`LinearClassifierEngine`):** Sub-millisecond linear classifier over structured and sparse/dense lexical features (typically $< 0.10\text{ ms}$ on local benchmarks).

Both engines run synchronously inside your process on standard CPU or Apple Silicon Metal. Neither engine requires GPU server infrastructure.

---

## 8. Revocation & deoptimization

When software runs in the real world, things change:
- Upstream schemas change
- Customer behavior shifts
- Business policies evolve

Ink treats deoptimization as a first-class operation. When verified error rates rise on the active path and reevaluation is triggered (via `reevaluate()`, `maintenance()`, or scheduled maintenance), Ink revokes the artifact's serving authority. The DecisionSite transitions back to `SHADOW`, and subsequent traffic routes safely to your Host model until new evidence is qualified.
