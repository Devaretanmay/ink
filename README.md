# Microloop

**A verified local Decision JIT for AI agents.**

Microloop turns repeated agent decisions into verified fast paths. An agent starts with its original model. Microloop records bounded decisions, evaluates site economics, compiles local candidate paths, runs them in shadow, and promotes them only after independent outcome verification. Unfamiliar or unverified states continue to use the original model.

```text
observe → profile → candidate → shadow → verified → active
                                  ↑                  │
                                  └── outcome drift ─┘
```

---

## Before & After Comparison

| Dimension | Direct Remote LLM | Naive Semantic Cache | Microloop Decision JIT |
| :--- | :--- | :--- | :--- |
| **Model Invocations** | 1,000 calls / 1k requests | 3–50 calls (blind cache hit) | **80–250 calls** (75–85% steady-state avoided) |
| **Decision Latency (p50)** | 120–250 ms | 2–10 ms | **0.30–0.45 ms** (~300x faster) |
| **Cost per 1k Decisions** | $2.00 – $3.50 | ~$0.05 | **$0.20 – $0.40** |
| **False Serves on Policy Drift** | 0 (follows prompt) | **30% – 66% corrupted serves** | **0.00% – 0.57%** (demotes to shadow in 1–4 calls) |
| **Serving Authority** | Ground truth model | Similarity threshold (> 0.8) | **Hoeffding concentration bound on holdout outcomes** |
| **Infrastructure Required** | Cloud API | Vector DB (Pinecone, Qdrant) + Redis | **Embedded local SQLite WAL (zero daemons)** |
| **External Dependencies** | Provider SDK | Embedding API + Network Cache | **None (pure Python + local embedded weights)** |

*Verified empirical numbers from `benchmarks/results/real_world_validation_v2.json` and `benchmarks/results/long_horizon_economics.json`.*

---

## When to Use Microloop (and When NOT To)

### When to Use:
1. **Repetitive bounded decisions:** Customer support routing, triage, tool dispatching, approval workflows, classification where the state repeat rate $\ge 50\%$.
2. **Measurable outcome feedback:** A downstream system produces a verification signal (e.g., successful API execution, refund approved, user confirmed).
3. **High fallback latency or cost:** Remote LLM calls costing $\ge \$0.001$/call or taking $\ge 100$ms where local sub-millisecond execution matters.
4. **Strict reliability requirements:** Environments where uncalibrated semantic hallucinations or stale cached decisions cannot be tolerated.

### When NOT to Use:
1. **Free-form generation:** Open-ended text generation, chat conversations, creative writing, or high-entropy outputs (>4.5 bits entropy).
2. **Exploratory, non-repetitive tasks:** Autonomous research, web navigation where every visited URL is unique (repetition $< 15\%$).
3. **Hyper-volatile policies:** Systems whose business rules change every few hundred decisions (drift interval $< 5,000$ calls).
4. **Zero verifier signal:** Workloads where decision correctness cannot be independently evaluated after execution.

---

## 30-Second Quickstart

Run the full Decision JIT lifecycle demo in under 3 seconds with zero external API keys:

```bash
# 1. Install microloop in your virtual environment
pip install -e python/microloop

# 2. Run the interactive 3-act terminal demo
python examples/thirty_second_demo.py
```

The demo executes three distinct acts:
- **Act 1: Observation & Economics:** Records baseline decisions and profiles repeat rate, entropy, and break-even horizon.
- **Act 2: Shadow Qualification:** Compiles candidate fast paths and qualifies against holdout verification evidence.
- **Act 3: Local Serving & Drift Protection:** Serves verified decisions in <0.5ms. Injects an upstream policy drift, autonomously demotes the stale path via comparison traffic, and safely returns to fallback with 99.6% fewer false serves than naive caching.

---

## Discovering Compilable Sites

Not every agent call should be compiled. Use the discovery tool to analyze your agent traces before compiling:

```python
from microloop.discovery import discover_from_file

candidates = discover_from_file("agent_traces.jsonl")
for c in candidates:
    print(f"Site: {c.site_name} | Repeat: {c.repetition_rate:.1%} | Action: {c.recommendation.upper()}")
```

Or profile an existing registered site directly:

```python
profile = client.profile("support.route")
print(profile.recommendation)        # 'strong_candidate', 'poor_repetition', 'weak_verifier', etc.
print(profile.break_even_decisions)  # Estimated decisions until qualification amortizes
```

---

## Integration Example

```python
from microloop import DecisionSite, Microloop, FallbackResult

site = DecisionSite(
    name="support.route",
    state_schema={"text": "string", "amount": "integer"},
    choices=("refund", "request_info", "specialist"),
    fallback_revision="1",
)

with Microloop() as client:
    client.register(site)

    # 1. Decide: serves locally if verified, else invokes fallback
    result = client.decide(
        site=site.name,
        state={"text": "Item damaged in shipping", "amount": 25},
        fallback=lambda: FallbackResult(my_llm_call(), cost=0.002, model_calls=1),
    )

    # 2. Execute action
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

## Claims and Evidence

Every claim made about Microloop is registered with its exact empirical conditions in the [Claims Registry](docs/claims.md).

- **Formal Invariants:** [Formal Safety Specification](docs/safety-spec.md)
- **Architecture Details:** [Architecture Documentation](docs/architecture.md)
- **CLI Commands:** [CLI Reference](docs/cli.md)

Run test suite:
```bash
pytest python/microloop/tests/
```

License: Apache-2.0
