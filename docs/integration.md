# Integration Guide

This guide describes how to integrate Ink into Python applications and agent frameworks.

---

## 1. Synchronous Integration

Wrap your existing model call with `ink.decide`:

```python
from ink import DecisionSite, Ink

ink = Ink(".ink/decisions.db")

site = DecisionSite(
    name="order.fraud_check",
    state_schema={"amount": "number", "country": "string"},
    choices=("allow", "review", "block"),
)
ink.register(site)

# Runtime call
state = {"amount": 250.0, "country": "US"}
result = ink.decide(
    site=site,
    state=state,
    fallback=lambda: call_fraud_model(state),
)

# Use result
action = result.choice
```

---

## 2. Asynchronous Integration

For async runtimes (FastAPI, asyncio):

```python
result = await ink.decide_async(
    site=site,
    state=state,
    fallback=lambda: call_fraud_model_async(state),
)
```

---

## 3. Function Decorator Ergonomics

Use the `@ink.wrap` decorator to instrument existing routing functions without restructuring code:

```python
@ink.wrap(site)
def route_request(state: dict) -> str:
    # This function acts as the Host fallback
    return call_remote_llm(state)

# Automatically queries Fast Path first, falling back to route_request
choice = route_request({"query": "Need technical assistance"})
```

---

## 4. LangGraph Integration

Accelerate conditional edges in LangGraph workflows:

```python
from ink import DecisionSite, Ink

ink = Ink(".ink/decisions.db")
site = DecisionSite("workflow.edge_router", choices=("continue", "retry", "escalate"))

def ink_edge(state: dict) -> str:
    result = ink.decide(
        site=site,
        state={"error_count": state.get("errors", 0)},
        fallback=lambda: supervisor_llm(state),
    )
    # Store decision ID in state for downstream outcome verification
    state["_decision_id"] = result.decision_id
    return result.choice

# Attach to graph
workflow.add_conditional_edges("supervisor", ink_edge, {
    "continue": "worker_node",
    "retry": "retry_node",
    "escalate": "human_node",
})
```

---

## 5. PydanticAI Integration

Accelerate tool selection in PydanticAI agents:

```python
from pydantic_ai import Agent
from ink import DecisionSite, Ink

ink = Ink(".ink/decisions.db")
site = DecisionSite("agent.tool_gate", choices=("query_db", "fetch_api", "ask_user"))

agent = Agent("openai:gpt-4o")

@agent.tool_plain
def select_action(user_prompt: str) -> str:
    result = ink.decide(
        site=site,
        state={"prompt": user_prompt},
        fallback=lambda: agent.run_sync(f"Select action for: {user_prompt}").data,
    )
    return result.choice
```
