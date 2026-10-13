# Ink Python SDK

**Models handle novelty. Ink turns proven behavior into software.**

Ink is a local runtime for production AI teams whose agents make repeated, bounded,
verifiable decisions. It records those decisions and their independent outcomes, shadows
candidate behavior, qualifies only what proves itself, serves it locally as a **Fast
Path**, and deoptimizes automatically when reality changes. Unfamiliar or unverified
states keep using your original model.

```text
observe → candidate → shadow → qualify → active → compare → deopt (on drift)
```

---

## Install

```bash
pip install ink-jit
# Local development:
pip install -e python/ink
```

The import is `ink`; the CLI is `ink`. Requirements: Python 3.11–3.13 on Apple Silicon
macOS or Linux.

---

## Evaluate first

Before writing integration code, point discovery at traces you already have:

```bash
ink discover traces.jsonl
```

It reports where repeated, bounded, verifiable behavior exists — and says so plainly when
it does not. See the project [README](../../README.md) and
[docs/discovery.md](../../docs/discovery.md).

---

## Minimal integration

```python
from ink import DecisionSite, FallbackResult, Ink

site = DecisionSite(
    name="support.route",
    state_schema={"text": "string", "amount": "integer"},
    choices=("refund", "request_info", "specialist"),
    fallback_revision="1",
)

with Ink() as client:
    client.register(site)

    # Serves locally once qualified; calls your model otherwise.
    result = client.decide(
        site=site,
        state={"text": "Item damaged in shipping", "amount": 25},
        fallback=lambda: FallbackResult(my_agent_llm(), model_calls=1, cost=0.002),
    )

    receipt = execute_action(result.choice)

    # The independent outcome is what authorizes the Fast Path.
    client.record_outcome(
        result.decision_id,
        quality=1.0 if receipt.success else 0.0,
        verifier="fulfillment_system",
        verifier_version="1",
        evidence={"order_id": receipt.order_id},
    )
```

---

## Inspection

```bash
ink sites  --db .ink/decisions.db
ink status support.route --db .ink/decisions.db
ink value  --db .ink/decisions.db
```

From Python:

```python
profile = client.profile(site)
print(profile.recommendation)        # 'strong_candidate', 'poor_repetition', ...
print(profile.break_even_decisions)  # decisions until qualification amortizes
```

---

## Development

```bash
pytest python/ink/tests/
python examples/thirty_second_demo.py
```

License: Apache-2.0.
