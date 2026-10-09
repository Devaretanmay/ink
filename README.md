# Ink

Your AI should not think twice about what it already knows.

Ink is a Behavior JIT for production AI systems.

It observes repeated bounded decisions, verifies their outcomes, and compiles trusted behavior into fast local software.

> Models handle novelty. Ink turns proven behavior into software.

---

## Install

```bash
pip install ink-jit
```

---

## Quick Example

```python
from ink import DecisionSite, Ink

# 1. Initialize local evidence storage
ink = Ink(".ink/decisions.db")

# 2. Define a bounded DecisionSite
site = DecisionSite(
    name="support.route",
    choices=("billing", "technical", "account"),
)

# 3. Resolve decisions: serves locally (< 0.1ms) when qualified; calls Host when novel
state = {"query": "Where can I view my March invoice?"}
result = ink.decide(site, state, fallback=lambda: call_llm(state))

# 4. Record the verified outcome after action completes
ink.record_outcome(result.decision_id, quality=1.0)
```

---

## Why Ink

Production agents spend large budgets calling frontier models for repeated decisions:
- Routing customer tickets
- Choosing agent tools
- Escalating incident alerts
- Filtering workflow requests

After enough verified outcomes, these decisions become predictable.

```text
WITHOUT INK
Every Request ──────► Frontier Model ──────► $0.02 + 800ms latency


WITH INK
Novel Request ──────► Host Model     ──────► Verified Outcome ──► Compile
Qualified Request ──► Fast Path      ──────► $0.00 + 0.10ms latency
```

---

## How It Works

Ink operates across three distinct planes:

```text
SERVING PATH (Sub-Millisecond)

Exact Match (< 0.01ms)
  ↓ miss
Linear Boundary (< 0.10ms)
  ↓ miss / novel
Host Model Fallback
```

1. **Observe**: New requests execute through your Host model. Ink records states and real outcomes in local SQLite storage.
2. **Compile**: When enough verified outcomes accumulate, Ink trains candidate local execution engines.
3. **Qualify**: Ink evaluates candidates against independent holdout data. A candidate must satisfy the site error budget under a 95% Wilson confidence lower bound.
4. **Serve**: Qualified candidates earn serving authority and execute locally in sub-milliseconds.
5. **Protect**: Novel, uncertain, or ambiguous states automatically fall back to the Host model. If real outcomes degrade, Ink revokes authority instantly.

---

## Why Not "Just Train a Classifier"?

A classifier is a model. Ink is a lifecycle.

A static classifier cannot:
- Decide when it is safe to serve traffic.
- Detect distribution shift in production.
- Demote itself back to the Host when business rules change.
- Guarantee statistical error budgets on live traffic.

Ink manages the lifecycle: observe, compile, qualify, serve, verify, detect drift, and revoke.

---

## When to Use Ink

### Good DecisionSites
- **Tool Selection**: Picking tools from a fixed set in an agent graph.
- **Triage & Routing**: Dispatching requests into explicit categories.
- **Approval Gates**: Determining whether to approve, review, or deny transactions.
- **Escalation**: Deciding whether an automated workflow requires human review.

### Poor DecisionSites
- **Open-Ended Writing**: Generating essays, marketing copy, or open dialogue.
- **Unbounded Arguments**: Extracting arbitrary code or raw text.
- **Zero-Feedback Tasks**: Workflows where outcomes cannot be verified.

---

## Production Safety

- **Confidence does not grant authority.** A model predicting high confidence is not proof of correctness.
- **Similarity does not grant authority.** Vector proximity does not guarantee safe execution.
- **Neural models never serve live requests.** `ink-decision-small` operates only in the background compiler.
- **Fail-open resilience.** If any internal error occurs, Ink routes immediately to the Host model.

---

## Empirical Evidence

On internal evaluations across bounded production workloads:

| Workload | Serving Engine | Local Serving Rate | Local Error Rate | 95% Wilson Lower Bound |
| :--- | :--- | :--- | :--- | :--- |
| **Tool Routing** | Linear | 78.4% | 0.0% | 98.49% |
| **Support Triage** | Exact | 42.3% | 0.0% | 96.80% |
| **Incident Triage** | Exact | 55.6% | 0.0% | 97.20% |

- **Zero Hallucination**: Qualified Fast Paths caused 0 errors across evaluated holdout sets.
- **Latency**: Local decisions resolved in under $0.10\text{ ms}$, saving an average of $650\text{ ms}$ per call.

*Note: Controlled internal benchmarks. External design-partner validation is currently in progress.*

---

## Documentation

- [Getting Started](docs/getting-started.md) — First Fast Path in 5 minutes
- [Concepts](docs/concepts.md) — DecisionSites, Fast Paths, and verified outcomes
- [Architecture](docs/architecture.md) — Serving path, control plane, and model roles
- [Production Safety](docs/production-safety.md) — Statistical qualification and drift demotion
- [Integration](docs/integration.md) — LangGraph, PydanticAI, and async adapters
- [Deployment](docs/deployment.md) — SQLite persistence and multi-node fleets
- [Benchmarks](docs/benchmarks.md) — Full benchmark methodology and replication
- [CLI Reference](docs/cli.md) — Local inspection, maintenance, and observability

---

## Project Status & License

Ink is under active development. The runtime and qualification system have internal validation.

Released under the [Apache-2.0 License](LICENSE).
