# Ink Python SDK

**Turn repeated agent decisions into verified fast paths.**

Ink is a verified local Decision JIT for AI agents. An agent starts with its original model. Ink records bounded decisions, profiles site economics, builds local candidate paths, runs them in shadow, and promotes them only after independent outcome verification. Unfamiliar or unverified states continue to use the original model.

```text
observe → profile → candidate → shadow → verified → active
                                  ↑                  │
                                  └── outcome drift ─┘
```

---

## 1. Quick Installation

```bash
pip install -e python/ink
```

Requirements: Python 3.11–3.13 on Apple Silicon macOS 14+ or Linux x86_64.

---

## 2. In-Loop Agent Usage

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

    # 1. Decide: local fast path (<0.5ms) or fallback LLM
    result = client.decide(
        site=site.name,
        state={"text": "Item damaged in shipping", "amount": 25},
        fallback=lambda: FallbackResult(my_agent_llm(), model_calls=1, cost=0.002),
    )

    # 2. Host executes action
    receipt = execute_action(result.choice)

    # 3. Record outcome for statistical qualification and drift monitoring
    client.record_outcome(
        result.decision_id,
        quality=1.0 if receipt.success else 0.0,
        verifier="fulfillment_system",
        verifier_version="1",
        evidence={"order_id": receipt.order_id},
    )
```

---

## 3. Site Profiling & Discovery

Evaluate decision site viability before spending time qualifying:

```python
# Profile an active site
profile = client.profile(site)
print(profile.recommendation)        # 'strong_candidate', 'poor_repetition', etc.
print(profile.break_even_decisions)  # Decisions until qualification amortizes

# Discover candidates from raw JSONL agent traces
from ink.discovery import discover_from_file

candidates = discover_from_file("agent_traces.jsonl")
for c in candidates:
    print(f"{c.site_name:25} | {c.repetition_rate:.1%} repeat | {c.recommendation.upper()}")
```

---

## 4. Local Fast Paths & Semantic Coverage

Ink supports both exact-state matching and sparse TF-IDF semantic coverage:
- **Exact Hash Engine:** Microsecond exact canonical JSON state matching.
- **Sparse TF-IDF Semantic Engine:** Character n-gram representation (0.012ms latency, 0.5MB memory) with negative-margin safety bounds and counterexample contraction.
- **Ink Decision Model v1:** Fine-tuned ModernBERT decision model with temperature-scaled calibration heads.

---

## 5. Development & Testing

```bash
# Run the test suite
pytest python/ink/tests/

# Run interactive 3-act terminal demo
python examples/thirty_second_demo.py
```

License: Apache-2.0
