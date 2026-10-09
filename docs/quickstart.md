# Quickstart Guide

This guide walks you through integrating Ink into an existing production AI application in under 5 minutes.

---

## 1. Installation

Install the core Ink runtime:

```bash
pip install ink-jit
```

*Note: Ink requires only `numpy` for core and exact/linear Fast Paths. No GPU or external cloud account is required.*

---

## 2. Identify a Bounded Decision

Find a place in your codebase where an LLM is called to make a bounded choice among discrete options:

```python
# Before Ink: Expensive remote LLM call every time
def route_support_ticket(ticket: dict) -> str:
    prompt = f"Categorize this ticket as billing, technical, or general: {ticket['text']}"
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip().lower()
```

---

## 3. Wrap the Function with Ink

Define a `DecisionSite` contract and decorate the function with `@ink.wrap`:

```python
from ink import DecisionSite, Ink

ink = Ink()

ticket_site = DecisionSite(
    name="ticket.triage",
    description="Customer inquiry routing boundary",
    state_schema={"text": "string"},
    choices=("billing", "technical", "general"),
)

@ink.wrap(ticket_site)
def route_support_ticket(state: dict) -> str:
    # This host fallback function is ONLY called when Ink needs model reasoning
    prompt = f"Categorize this ticket as billing, technical, or general: {state['text']}"
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip().lower()
```

---

## 4. Record Real Outcomes

Whenever your application learns whether the decision was successful (e.g., ticket resolved, user satisfied, downstream task succeeded), record the outcome:

```python
from ink import Outcome

# Using the decision_id returned from route_support_ticket
decision = route_support_ticket({"text": "I was charged twice on my credit card"})

# When resolution is verified:
ink.record_outcome(
    decision.decision_id,
    Outcome(
        quality=1.0,  # 1.0 = verified success, 0.0 = failure
        verifier="support_resolution_team",
        verifier_version="v1",
        evidence={"resolved": True},
    ),
)
```

---

## 5. Observe and Serve

Once repeated traffic accumulates and is verified by outcomes, Ink compiles a local candidate engine. Run the CLI to monitor status:

```bash
# Check site states and serving metrics
ink status

# Launch the visual dashboard
ink console
```

When statistical qualification criteria are met, the site automatically advances to `ACTIVE`. Subsequent requests with proven states execute in **< 1ms with 0 API calls**.

---

## 6. Policy Model Configuration (Optional)

Ink includes a canonical Policy Model family to propose candidate decisions:

```python
# Default: ink-decision-small (~421M ModernBERT via MLX)
ink = Ink(policy_model="small")

# Optional: ink-decision-large (~486M DeBERTa-v3 / GLiNER2 tier)
ink = Ink(policy_model="large")

# Exact / Linear only (neural proposals disabled)
ink = Ink(policy_model=None)
```

You can also set the active tier via environment variable:
```bash
export INK_POLICY_MODEL=small   # Options: small, large, auto, disabled
```

Remember: **Policy models propose. Ink evidence grants authority.** Models possess zero serving authority until empirically validated by real outcomes.
