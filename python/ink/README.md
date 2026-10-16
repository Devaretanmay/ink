# Ink Python SDK

**Stop paying frontier models to make decisions they already solved yesterday.**

Ink is an embedded local runtime for production AI agents that make repeated, bounded decisions (tool picking, ticket routing, approval gates). It records decisions and their real-world outcomes, qualifies candidate behavior with statistical confidence intervals, serves proven behavior as a **Fast Path** in `< 0.1ms`, and automatically deoptimizes back to your model if reality changes.

```text
observe → candidate → shadow → qualify → active → compare → deopt (on drift)
```

---

## Install

```bash
pip install ink-jit

# Or for local development:
pip install -e python/ink
```

*Requirements: Python 3.11–3.13 on macOS (Apple Silicon Metal) or Linux. Zero external cloud dependencies.*

---

## Evaluate existing traces first

Before writing integration code, point Ink at traces you already have:

```bash
ink discover traces.jsonl --profile
```

It tells you where repeated, bounded, verifiable behavior exists in your logs—and says so plainly if your workload isn't a good fit.

---

## 2-Minute minimal integration

```python
from ink import DecisionSite, Ink

ink = Ink(".ink/decisions.db")

site = DecisionSite(
    name="support.route",
    state_schema={"text": "string", "amount": "integer"},
    choices=("refund", "request_info", "specialist"),
    local_error_budget=0.01,  # 99% accuracy required before local serving
)
ink.register(site)

# Serves locally in < 0.1ms once qualified; calls your model otherwise.
result = ink.decide(
    site=site,
    state={"text": "Item damaged in shipping", "amount": 25},
    fallback=lambda: call_expensive_agent_llm(),
)

# Execute the downstream action:
receipt = execute_action(result.choice)

# Feed back ground-truth proof. This is what earns the Fast Path its authority:
ink.record_outcome(
    decision_id=result.decision_id,
    quality=1.0 if receipt.success else 0.0,
    verifier="fulfillment_system",
    verifier_version="v1",
    evidence={"order_id": receipt.order_id},
)
```

Or use the `@ink.wrap` decorator:

```python
@ink.wrap("support.route", choices=["refund", "request_info", "specialist"])
def route_support(text: str, amount: int):
    return call_expensive_agent_llm(text, amount)

# Automatically queries Fast Path first, falling back to function on novelty:
res = route_support("Item damaged in shipping", 25)
print(res.choice)
```

---

## Open the local dashboard

```bash
ink ui
```

Opens a dark-mode halftone console on `http://localhost:4242` to inspect your decisions, check fast-path hit rates, and monitor drift.

---

## CLI quick commands

```bash
# Check registered sites and current lifecycle stage
ink sites --db .ink/decisions.db

# Inspect statistical qualification and Wilson confidence for a site
ink status support.route --db .ink/decisions.db

# Run maintenance to audit drift across active Fast Paths
ink maintenance --db .ink/decisions.db

# Check database and runtime health
ink doctor --db .ink/decisions.db
```

---

## Development & testing

```bash
# Run the test suite:
pytest python/ink/tests

# Run benchmarks:
python scripts/generate_evidence_manifest.py
```
