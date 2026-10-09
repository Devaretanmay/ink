# Core Concepts

Ink replaces repeated remote model calls with verified local software.

This document defines the primary concepts in Ink.

---

## DecisionSite

A **DecisionSite** is a bounded contract in your application. It specifies a decision point where an AI system selects one choice from a fixed set.

Every DecisionSite declares:
- `name`: Unique identifier for the decision boundary.
- `choices`: Tuple of valid target choices.
- `state_schema`: Dictionary of expected input field types.

Example:

```python
from ink import DecisionSite

site = DecisionSite(
    name="agent.tool_choice",
    choices=("search_kb", "issue_refund", "escalate_human"),
    state_schema={"user_intent": "string", "retries": "integer"},
)
```

Ink optimizes only bounded choices. Ink does not optimize open-ended generation.

---

## Host

The **Host** is your existing remote model or agent.

When a decision is novel, unverified, or ambiguous, Ink calls the Host. The Host always handles novelty.

Ink never replaces the Host on unseen inputs.

---

## Verified Outcome

A **verified outcome** is ground-truth feedback recorded after an action completes.

Ink does not trust model self-evaluations. Authority requires real evidence.

Verified outcomes can come from:
- User confirmations or corrections.
- Downstream tool execution signals (such as successful API calls or test passes).
- Deterministic rules and human reviews.

To record an outcome:

```python
ink.record_outcome(
    decision_id=result.decision_id,
    quality=1.0,  # 1.0 = correct, 0.0 = incorrect
    verifier="refund_api",
    verifier_version="v1",
)
```

---

## Fast Path

A **Fast Path** is a compiled, local execution engine that resolves decisions in memory without network calls.

Ink compiles two local serving tiers:
1. **Exact**: Sub-microsecond hash table match ($< 0.01\text{ ms}$).
2. **Linear**: Sub-millisecond sparse/dense linear classifier ($< 0.1\text{ ms}$).

Both tiers run locally inside your process. Neither tier calls a remote API.

---

## Lifecycle States

Every DecisionSite progresses through four lifecycle states:

```text
OBSERVE ──> SHADOW ──> ACTIVE ──> DEGRADED
```

1. **`OBSERVE`**: All traffic routes to the Host. Ink accumulates observations and outcomes.
2. **`SHADOW`**: Ink compiles a candidate artifact and evaluates it in parallel against live Host decisions.
3. **`ACTIVE`**: The candidate passed statistical qualification. Ink serves matching requests locally via Fast Path.
4. **`DEGRADED`**: Ink detected policy drift or quality drops. Serving authority is revoked, and traffic returns to the Host.

---

## Qualification

**Qualification** is the statistical gate required before a candidate artifact can serve traffic.

Ink evaluates candidates against independent evaluation data. The candidate's Wilson 95% confidence lower bound must clear the site's required error budget.

Point accuracy is not enough. Model confidence is not enough. Only verified statistical evidence grants serving authority.

---

## Discovery

**Discovery** is an optional inspection tool.

Ink analyzes execution trace files (`traces.jsonl`) to identify repeated decision boundaries and estimate potential cost and latency savings:

```bash
ink discover traces.jsonl
```
