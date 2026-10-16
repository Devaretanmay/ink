# Getting started with Ink

Ink is a Behavior JIT (Just-In-Time compiler) for production AI systems.

When an AI agent runs in production, it repeatedly calls expensive frontier models for bounded decisions: choosing a tool, routing a customer ticket, or picking a workflow branch. Ink records whether those decisions actually succeeded, qualifies proven behavior using statistical confidence intervals, and compiles it into local Python code that runs in **0.1 milliseconds for $0.00**.

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

*Supports Python 3.11, 3.12, and 3.13 on Linux and macOS (with Apple Silicon Metal acceleration). No cloud accounts or API keys required.*

---

## 2-Minute quickstart

Here is how you instrument a decision point, resolve decisions through Ink, and record real-world outcomes.

```python
from ink import DecisionSite, Ink

# 1. Initialize local evidence storage (SQLite in WAL mode)
ink = Ink(".ink/decisions.db")

# 2. Define a bounded DecisionSite contract
site = DecisionSite(
    name="support.route",
    choices=("billing", "technical", "account"),
    state_schema={"query": "string"},
    local_error_budget=0.01,  # 99% accuracy required before local serving
)
ink.register(site)

# 3. Define your existing slow/expensive model fallback
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
# When qualified: runs locally in < 0.1ms without network calls
state = {"query": "Where can I view my March invoice?"}
result = ink.decide(
    site=site,
    state=state,
    fallback=lambda: call_host_model(state),
)

print(f"Choice: {result.choice} | Source: {result.source}")
# Output: Choice: billing | Source: fallback (or fast_path once qualified)

# 5. Record verified outcome after downstream action completes
# Feed back ground-truth signals (API success, database commit, human approval):
ink.record_outcome(
    decision_id=result.decision_id,
    quality=1.0,  # 1.0 = verified success, 0.0 = error
    verifier="support_triage_team",
    verifier_version="v1",
)
```

---

## Or use the `@ink.wrap` decorator

If you don't want to change your function signature, wrap it directly:

```python
from ink import Ink

ink = Ink(".ink/decisions.db")

@ink.wrap("support.route", choices=["billing", "technical", "account"])
def route_ticket(query: str):
    # Your model call goes here
    return my_expensive_llm(query)

# Calls your model until qualified; runs locally in 0.08ms afterward:
decision = route_ticket("Where is my March invoice?")
print(decision.choice)
```

---

## Open the local console

Ink includes a zero-dependency, local halftone console to inspect your system:

```bash
ink ui
```

Open `http://localhost:4242` in your browser. You can:
- Watch your fast-path hit rate climb in real time.
- See total dollars and model calls saved.
- Inspect the raw state payload of past decisions.
- Inspect in-memory predictions against historical states without side effects.
- View serving policy status (`ACTIVE`, `CANARY`, `SHADOW`, `HOST_OVERRIDE`).

---

## The JIT lifecycle in practice

Every DecisionSite moves through 5 continuous stages:

1. **Observe:** 100% of traffic routes to your model. Ink records inputs, decisions, and real-world outcomes in local SQLite storage.
2. **Compile:** When enough verified evidence accumulates, Ink compiles local execution artifacts (`ExactEngine` hash table or `LinearClassifierEngine`).
3. **Qualify:** Ink tests candidates against independent holdout data. A candidate must satisfy your site's error budget under a **95% Wilson confidence lower bound**.
4. **Serve:** Once qualified, matching production requests run locally in sub-milliseconds with zero token bills.
5. **Verify:** Ink sends a small sample of live traffic to your model in parallel to check for drift. If error rates rise, Ink automatically demotes the artifact and hands traffic back to your model.

---

## Next steps

- **Category comparison:** Read [Why Ink](why-ink.md) for how Ink compares to caches, classifiers, and fine-tuning.
- **Audit your first decision:** Check out the [Decision audit](decision-audit.md) guide.
- **Learn the core concepts:** Explore [Concepts](concepts.md).
- **Inspect the architecture:** Read [Architecture](architecture.md).
- **Understand safety contracts:** Read [Production safety](production-safety.md).
- **Integrate with agent frameworks:** See [Integration](integration.md) for LangGraph and PydanticAI.
