# Integration guide

Here is how you integrate Ink into Python applications and agent frameworks.

---

## 1. Synchronous integration

Wrap your existing model call with `ink.decide`:

```python
from ink import DecisionSite, Ink

ink = Ink(".ink/decisions.db")

site = DecisionSite(
    name="order.fraud_check",
    state_schema={"amount": "number", "country": "string"},
    choices=("allow", "review", "block"),
    local_error_budget=0.01,
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

## 2. Asynchronous integration

For async runtimes (FastAPI, asyncio, Starlette):

```python
result = await ink.decide_async(
    site=site,
    state=state,
    fallback=lambda: call_fraud_model_async(state),
)
```

---

## 3. Function decorator ergonomics (`@ink.wrap`)

Use the `@ink.wrap` decorator to instrument existing routing functions without restructuring your code:

```python
from ink import Ink

ink = Ink(".ink/decisions.db")

@ink.wrap("order.fraud_check", choices=["allow", "review", "block"])
def route_fraud(state: dict) -> str:
    # This function acts as the Host fallback
    return call_remote_llm(state)

# Automatically queries Fast Path first, falling back to route_fraud:
result = route_fraud({"amount": 250.0, "country": "US"})
print(result.choice)
```

---

## 4. LangGraph integration

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

## 5. PydanticAI integration

Accelerate tool selection in PydanticAI agents:

```python
from ink import Ink

ink = Ink(".ink/decisions.db")

@ink.wrap("agent.tool_selector", choices=["search_db", "fetch_url", "final_answer"])
def select_tool(query: str):
    return agent_model.select_tool(query)

# Use in your agent loop
tool = select_tool(user_prompt)
```
