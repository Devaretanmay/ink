# Getting Started with Ink

Ink is the Behavior JIT for production AI systems.

Production AI systems accumulate behavioral knowledge over time. Once that behavior is independently proven, it should become software instead of being rented repeatedly from a general-purpose model.

> **Models handle novelty. Ink turns proven behavior into software.**

---

## Installation

Install Ink using pip:

```bash
pip install ink-jit
```

Or using uv:

```bash
uv add ink-jit
```

Ink supports Python 3.11, 3.12, and 3.13 on Linux and macOS (including Apple Silicon Metal acceleration).

---

## 5-Minute Quickstart

This example registers a bounded DecisionSite, resolves decisions through Ink, and records verified outcomes.

```python
from ink import DecisionSite, Ink

# 1. Initialize local evidence storage (SQLite with WAL mode)
ink = Ink(".ink/decisions.db")

# 2. Define a bounded DecisionSite
site = DecisionSite(
    name="support.route",
    description="Customer ticket dispatch queue",
    choices=("billing", "technical", "account"),
    state_schema={"query": "string"},
)
ink.register(site)

# 3. Define your existing Host model fallback
def call_host_model(state: dict) -> str:
    # Your existing LLM call (e.g. Claude, GPT-4o, Gemini)
    query = state["query"].lower()
    if "invoice" in query or "payment" in query:
        return "billing"
    if "bug" in query or "error" in query:
        return "technical"
    return "account"

# 4. Resolve decisions through Ink
# When unverified: routes to call_host_model
# When qualified: executes local Fast Path in sub-milliseconds
state = {"query": "Where can I view my March invoice?"}
result = ink.decide(
    site=site,
    state=state,
    fallback=lambda: call_host_model(state),
)

print(f"Choice: {result.choice} | Source: {result.source}")
# Output: Choice: billing | Source: fallback

# 5. Record verified outcome after downstream action completes
# Outcome provenance matters: feed back real execution signals
ink.record_outcome(
    decision_id=result.decision_id,
    quality=1.0,  # 1.0 = verified success, 0.0 = verified error
    verifier="support_triage_team",
    verifier_version="v1",
)
```

---

## The JIT Lifecycle in Practice

Every DecisionSite progresses through five continuous stages:

1. **Observe**: Initial traffic routes 100% to your Host model. Ink records states, choices, and verified outcomes in local SQLite storage.
2. **Compile**: When sufficient verified evidence accumulates, Ink compiles candidate local execution engines (`ExactEngine` or `LinearClassifierEngine`).
3. **Qualify**: Ink evaluates candidates against independent holdout data. A candidate must satisfy the site error budget under a 95% Wilson confidence lower bound.
4. **Serve**: Once qualified, matching production requests execute locally in sub-milliseconds without token fees or network latency.
5. **Verify**: Ink samples live traffic to detect drift. When reevaluation detects rising error rates, Ink revokes local authority back to the Host model.

---

## Next Steps

- **Understand the Category**: Read [Why Ink](why-ink.md) for comparisons against caches, classifiers, and fine-tuning.
- **Audit Your First Decision**: See [Decision Audit](decision-audit.md) to evaluate your highest-volume model boundaries.
- **Learn the Core Primitives**: Explore [Concepts](concepts.md).
- **Inspect System Architecture**: Read [Architecture](architecture.md).
- **Review Safety Contracts**: Read [Production Safety](production-safety.md).
- **Integrate with Frameworks**: See [Integration](integration.md) for LangGraph and PydanticAI adapters.
