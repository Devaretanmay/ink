# Integrating Microloop

This guide covers integrating the Microloop Decision JIT into your AI agent systems, from simple routing functions to multi-step autonomous agent loops (such as browser-use, LangGraph, or custom agent frameworks).

---

## 1. Installation

Install Microloop directly into your environment:

```bash
pip install -e python/microloop
```

Verify the installation:
```bash
python -c "import microloop; print(microloop.__version__)"
```

---

## 2. Core Integration Pattern

Integrating Microloop requires three steps:
1. **Define a DecisionSite:** Declare the input state schema and discrete choices.
2. **Dispatch Decisions:** Call `client.decide()` with your original LLM fallback.
3. **Record Outcomes:** Call `client.record_outcome()` with independent verification receipts.

```python
from microloop import DecisionSite, Microloop, FallbackResult

# 1. Declare the DecisionSite
site = DecisionSite(
    name="support.route",
    state_schema={"text": "string", "amount": "integer"},
    choices=("refund", "request_info", "specialist"),
    fallback_revision="1",
)

with Microloop() as client:
    client.register(site)

    # 2. Decide: Serves locally (<0.5ms) if verified; otherwise calls fallback
    result = client.decide(
        site=site.name,
        state={"text": "Item damaged in shipping", "amount": 25},
        fallback=lambda: FallbackResult(
            choice=call_remote_llm(),
            model_calls=1,
            cost=0.002,
        ),
    )

    # Host executes the chosen action
    receipt = execute_action(result.choice)

    # 3. Record outcome for statistical qualification and drift monitoring
    client.record_outcome(
        result.decision_id,
        quality=1.0 if receipt.success else 0.0,
        verifier="fulfillment_system",
        verifier_version="1",
        evidence={"receipt_id": receipt.id},
    )
```

---

## 3. Integration Playbooks

### 3.1 Browser Automation & Web Agents (e.g. browser-use)
Web agents repeatedly decide between browser actions (click, input, scroll, navigate) given page state. Repetitive checkout, navigation, or data extraction workflows have high state repetition.

```python
from microloop import DecisionSite, Microloop, FallbackResult

browser_site = DecisionSite(
    name="browser.action_dispatch",
    state_schema={"dom_selector": "string", "page_url": "string"},
    choices=("click", "fill_input", "scroll_down", "finish"),
    fallback_revision="1",
)

client = Microloop()
client.register(browser_site)

def execute_browser_step(page_state):
    # Intercept agent tool selection
    result = client.decide(
        site="browser.action_dispatch",
        state={"dom_selector": page_state.selector, "page_url": page_state.url},
        fallback=lambda: FallbackResult(agent_llm.predict_action(page_state), 1, cost=0.003),
    )

    # Execute browser action
    dom_result = browser.dispatch(result.choice)

    # Outcome receipt from DOM state change (e.g. navigation succeeded)
    client.record_outcome(
        result.decision_id,
        quality=1.0 if dom_result.valid else 0.0,
        verifier="dom_mutation_verifier",
        verifier_version="1",
        evidence={"dom_changed": dom_result.changed},
    )
    return dom_result
```

### 3.2 LangGraph / LangChain StateGraph Node Routing
In multi-agent StateGraphs, routing nodes classify customer requests or choose next agent nodes.

```python
from microloop import DecisionSite, Microloop, FallbackResult

router_site = DecisionSite(
    name="agent.node_router",
    state_schema={"last_user_message": "string", "turn_count": "integer"},
    choices=("billing_agent", "tech_support_agent", "triage_agent"),
    fallback_revision="1",
)

client = Microloop()
client.register(router_site)

def router_node(state):
    result = client.decide(
        site="agent.node_router",
        state={
            "last_user_message": state["messages"][-1].content,
            "turn_count": len(state["messages"]),
        },
        fallback=lambda: FallbackResult(llm_router.invoke(state), 1, cost=0.0015),
    )
    # Return destination node
    return {"next_node": result.choice, "decision_id": result.decision_id}
```

---

## 4. Economic Profiling & Discovery

Before compiling a site, evaluate whether it will produce positive economic returns.

### 4.1 In-Process Site Profiler
Inspect an active site's local history directly:

```python
profile = client.profile(site)
print(f"Repetition Rate:     {profile.exact_repeat_rate:.1%}")
print(f"Choice Entropy:      {profile.choice_entropy:.2f} bits")
print(f"Verifier Quality:    {profile.verifier_quality:.1%}")
print(f"Recommendation:      {profile.recommendation.upper()}")
print(f"Amortization Target: {profile.break_even_decisions} decisions")
```

If `profile.recommendation == "poor_repetition"` or `"weak_verifier"`, compilation is refused to save resources.

### 4.2 Discovering Candidate Sites from Execution Logs
You can run the discovery tool on historical agent logs (JSON/JSONL) before instrumenting code:

```python
from microloop.discovery import discover_from_file

candidates = discover_from_file("agent_traces.jsonl")
for site in candidates:
    print(
        f"Site: {site.site_name:25} | "
        f"Repetition: {site.repetition_rate:6.1%} | "
        f"Savings: ${site.estimated_annual_savings:8.2f}/yr | "
        f"Action: {site.recommendation.upper()}"
    )
```

---

## 5. Lifecycle Compilation & Maintenance

Once a site accumulates $\ge 50$ observations and exhibits favorable economics:

```python
# 1. Compile candidate fast path
client.compile(site, engine="exact")  # or engine="decision" for neural

# 2. Calibrate coverage regions with negative margins
def my_verifier(state, choice):
    # Isolated offline verification
    return Outcome(1.0 if verify_correctness(state, choice) else 0.0, "verifier", "1", {})

client.calibrate(site, verifier=my_verifier)

# 3. Shadow evaluation and atomic auto-promotion
client.evaluate(site, verifier=my_verifier, auto_promote=True)

# 4. Periodic maintenance (run in background cron or celery job)
client.maintenance(site=site.name, verifier=my_verifier)
```

Maintenance runs non-blockingly, checks for drift, and automatically promotes or demotes artifacts.
