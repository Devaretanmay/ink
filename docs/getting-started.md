# Getting Started with Ink

Ink is a Behavior JIT for production AI systems.

It observes repeated bounded decisions, records verified outcomes, and compiles trusted behavior into fast local software.

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

Ink supports Python 3.11, 3.12, and 3.13 on Linux and macOS.

---

## Quickstart

This example shows how to register a DecisionSite, execute decisions, and record verified outcomes.

```python
from ink import DecisionSite, Ink, Outcome

# 1. Initialize the Ink client with a local evidence database
ink = Ink(".ink/decisions.db")

# 2. Define a bounded DecisionSite
site = DecisionSite(
    name="support.route",
    description="Customer support ticket dispatch",
    state_schema={"query": "string"},
    choices=("billing", "technical", "account"),
)
ink.register(site)

# 3. Define your existing Host model fallback
def call_host_model(state: dict) -> str:
    # Your existing LLM call (e.g. OpenAI, Anthropic, Gemini)
    query = state["query"].lower()
    if "invoice" in query or "card" in query:
        return "billing"
    if "error" in query or "bug" in query:
        return "technical"
    return "account"

# 4. Route traffic through Ink
state = {"query": "Where can I view my March invoice?"}
result = ink.decide(
    site=site,
    state=state,
    fallback=lambda: call_host_model(state),
)

print(f"Choice: {result.choice} | Source: {result.source}")
# Output: Choice: billing | Source: fallback

# 5. Record verified outcome after downstream action completes
ink.record_outcome(
    result.decision_id,
    quality=1.0,  # 1.0 = verified success, 0.0 = failure or correction
    verifier="support_triage_team",
    verifier_version="v1",
)
```

---

## How It Works

1. **Initial State (`OBSERVE`)**:
   Ink runs in observation mode. All decisions route to your Host model. Ink records states, choices, and outcomes in local SQLite storage.

2. **Compilation**:
   When Ink observes enough verified outcomes, it builds a candidate local execution engine (such as an exact hash index or linear boundary).

3. **Qualification (`SHADOW`)**:
   Ink tests the candidate against independent evaluation data. The candidate must statistically satisfy the site's required error budget.

4. **Local Fast Path (`ACTIVE`)**:
   Once qualified, matching traffic executes locally in sub-milliseconds without calling the Host model.

5. **Novelty Fallback**:
   If a new request is outside the qualified boundary or novel, Ink automatically calls the Host model.

---

## Next Steps

- Understand core primitives in [Concepts](concepts.md).
- Learn about the three planes in [Architecture](architecture.md).
- Read how Ink prevents hallucinations in [Production Safety](production-safety.md).
- Explore framework adapters in [Integration](integration.md).
