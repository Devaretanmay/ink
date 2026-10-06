# Ink

**Behavior JIT for production AI.**

AI systems repeatedly make bounded decisions — routing, tool selection, escalation, classification. Ink learns which of those decisions have become predictable **and** independently verifiable. Qualified behavior becomes a local Fast Path. Novel or uncertain states continue to the original model. If behavior drifts, Ink revokes the Fast Path automatically.

> Models handle novelty. Ink turns proven decisions into software.

```text
observe → candidate → shadow → qualify → active → compare → deopt (on drift)
```

---

## Why Ink exists

Production AI agents make the same bounded decisions repeatedly. Each call costs money and adds latency, even when the answer hasn't changed. But blindly caching AI responses is dangerous — stale decisions cause real harm, and there's no built-in mechanism to detect when they go wrong.

Ink bridges this gap: it identifies repeated decisions, proves they're correct against real outcomes, and serves exact matches locally in under 0.2 ms (learned local decisions in ~48 ms). When reality changes, it detects drift and falls back to the model automatically.

## What Ink optimizes

- **Repeated bounded decisions** — routing, triage, tool selection, classification with repeat rate ≥ 20%
- **Decisions with measurable outcomes** — a downstream system can verify whether the decision was correct
- **High-latency or high-cost model calls** — remote LLM calls ≥ 100 ms or meaningful per-call cost
- **Workloads where policy drift matters** — you need stale decisions detected and revoked automatically

## What Ink does NOT optimize

- Free-form text generation
- Creative or exploratory tasks
- Decisions that can't be independently verified
- Workloads with very low repetition
- High-entropy research or planning

If your workload doesn't have bounded, repeating, verifiable decisions, `ink discover` will tell you. That's a feature.

---

## Ink is not

| Often confused with | What it does | How Ink differs |
| :--- | :--- | :--- |
| **Semantic cache** | Reuses responses to similar prior inputs | Ink does not use similarity. It requires independent outcome evidence before serving. |
| **Model router** | Chooses which model handles a request | All router paths still call a model. Ink may eliminate the call entirely. |
| **Agent framework** | Orchestrates agent steps and tools | Ink is a library inside your existing agent, not a replacement for it. |
| **General small-model replacement** | Replaces a large model with a smaller one | Ink's local model is part of the qualification engine, not a standalone replacement. |
| **Workflow engine** | Automates multi-step business processes | Ink optimizes individual decision points, not workflows. |
| **LLM gateway** | Proxies and manages LLM API calls | Ink runs locally with zero network calls during inference. |

**Semantic cache** reuses similar prior responses. **Router** chooses which model to call. **Ink** may eliminate the model call entirely — but only after repeated behavior is independently qualified and continuously monitored.

---

## Evaluate Ink in 10 minutes

### 1. Install

```bash
pip install ink-jit
```

This installs the Ink runtime including the integrated local decision model (421M MLX backend on Apple Silicon, MLX CPU on Linux). No separate extras required.

### 2. Discover candidate DecisionSites

Analyze your existing traces without changing production code:

```bash
ink discover traces.jsonl
```

```text
Found 8 candidate call sites.

1. support.route
   traffic: 840/day (2,520 observed)
   repetition: 72.0%
   choices: 3 ['refund', 'request_info', 'specialist']
   verifier readiness: VERIFIER_READY (85.0% coverage)
   recommendation: STRONG CANDIDATE
   reason: Strong candidate: 72.0% repetition, bounded choices (3), break-even in ~158 decisions.

2. agent.tool_select
   traffic: 420/day (1,260 observed)
   repetition: 58.0%
   choices: 4 ['search', 'calculate', 'lookup', 'respond']
   verifier readiness: VERIFIER_READY (90.0% coverage)
   recommendation: STRONG CANDIDATE

3. response.generate
   recommendation: IGNORE
   reason: High output entropy (6.20 bits, 128 choices, avg length 245 chars); free-form generation is not bounded.
```

Add `--profile` for economic detail. Add `--snippet` for integration code. See [docs/discovery.md](docs/discovery.md) for trace format.

### 3. Integrate one DecisionSite

```python
from ink import DecisionSite, Ink, FallbackResult

site = DecisionSite(
    name="support.route",
    state_schema={"text": "string", "amount": "integer"},
    choices=("refund", "request_info", "specialist"),
)

with Ink() as engine:
    # Decide: serves locally when qualified, else calls your model
    result = engine.decide(
        site=site,
        state={"text": "Item damaged in shipping", "amount": 25},
        fallback=lambda: FallbackResult(my_llm_call(), cost=0.002, model_calls=1),
    )

    # Execute the action
    receipt = execute_action(result.choice)

    # Record the real outcome — this is how Ink qualifies decisions
    engine.record_outcome(
        result.decision_id,
        quality=1.0 if receipt.success else 0.0,
        verifier="fulfillment_system",
        verifier_version="1",
        evidence={"order_id": receipt.order_id},
    )
```

### 4. Inspect site lifecycle

```bash
ink sites --db .ink/decisions.db
ink status support.route --db .ink/decisions.db
```

Sample trace data for discovery is available in `examples/discovery/`.

---

## Why qualification matters

A local model being confident is **not** enough to serve a decision. Ink separates _candidate generation_ from _serving authority_.

Fast Path authority comes from:

1. **Independent outcomes** — real downstream systems report whether the decision was correct
2. **Shadow comparison** — the candidate runs alongside the model before it can serve
3. **Qualification thresholds** — statistical tests confirm the fast path matches model quality
4. **Ongoing comparison traffic** — 5–35% of decisions continue to the model for monitoring
5. **Automatic deoptimization** — if quality degrades, the fast path is revoked

```text
OBSERVE    Your agent runs normally. Ink records decisions and outcomes.
    ↓
CANDIDATE  Ink estimates repetition, entropy, and qualification cost.
    ↓
SHADOW     The candidate runs alongside the model. Outcomes are compared.
    ↓
QUALIFY    Statistical tests confirm match quality (candidate ≠ authority).
    ↓
ACTIVE     Qualified decisions serve locally (exact: <0.2 ms, learned: ~48 ms).
    ↓
COMPARE    Ongoing comparison traffic (5–35%) monitors for drift.
    ↓
DEOPT      If quality degrades, Ink revokes the fast path automatically.
```

> **Ink may still serve wrong decisions before drift is detected.** In the benchmark, 7–8 wrong serves occurred before demotion. This is materially safer than static caching (12–18% ongoing error rate under the same conditions), but it is not zero.

---

## How the model fits

Ink includes a trained local decision model as part of its Decision Engine:

```text
Ink
├── DecisionSite contract
├── Exact-state decision tier
├── Trained local decision model (421M, ink-decision-v1)
├── Qualification lifecycle
├── Outcome verification
├── Fast Path serving
└── Deoptimization
```

The model is not an optional add-on. It is part of how Ink generates candidate decisions for qualification. Exact-state matching handles repeated identical inputs; the learned model extends coverage to semantically similar states within qualified regions.

---

## Measured results

From the [competitive benchmark](benchmarks/results/competitive_frontier/REPORT.md) — 18,000 decisions across 4 workloads with strict 70/30 temporal evaluation:

| Metric | Measured | Scope |
| :--- | :--- | :--- |
| Exact fast-path latency | 0.18–0.19 ms (p50) | Qualified exact-match local serves only |
| Learned local decision latency | 47.71 ms (p50) | Local model inference (no remote call) |
| Remote fallback latency | Provider-dependent | Unqualified decisions → original model |
| DecisionSite call reduction | 19.9–40.4% | Within bounded decision sites |
| Whole-app call reduction | 3.99–10.10% | Entire application (sites = 15–25% of traffic) |
| Whole-app spend reduction | 3.51–8.87% | Entire application |
| Wrong serves before demotion | 7–8 | Under injected passive policy drift |
| Static cache wrong-serve rate | 12.0–18.0% | Same drift conditions |
| High-entropy workload | Correctly rejected | Refused compilation (0% wasted resources) |

> **Latency tiers:** Exact fast paths serve in <0.2 ms for previously-seen state+choice combinations. Learned local decisions use the local model (~48 ms) to handle unseen-but-similar states without a remote call. Decisions that don't qualify for either tier fall back to the original remote model.
>
> **Important denominators:** Bounded verifiable decisions typically represent 15–25% of total application LLM calls. Whole-application savings reflect this. 20–40% fewer model calls _inside qualified bounded decision sites_ is the correct framing.

### What Ink does NOT claim

- **Zero errors** — 7–8 wrong serves occurred before drift detection
- **80% company-wide cost reduction** — whole-app savings depend on bounded traffic share
- **Replacement of all model calls** — only bounded, repeating, verifiable decisions qualify
- **Faster entire applications** — 0.18 ms (exact) and ~48 ms (learned) apply to qualified local serves, not total workflow

---

## CLI

```bash
ink discover traces.jsonl          # Analyze traces for compilable sites
ink sites --db decisions.db        # List registered decision sites
ink status --db decisions.db       # Site lifecycle status
ink value --db decisions.db        # Value report: calls avoided, cost saved
ink inspect SITE --db decisions.db # Deep site inspection
```

## What happens if Ink is uncertain?

It calls your model. Ink never serves a decision it hasn't qualified through independent outcome verification. The `fallback` function runs normally — your agent behaves exactly as it did before Ink.

---

## Deployment and data handling

- **Storage:** Local SQLite at `.ink/decisions.db`. No cloud dependencies.
- **Network:** Zero network calls during `decide()` and `record_outcome()`. Model weights are downloaded once from HuggingFace on first use.
- **Retention:** `ink retain --days N` to compact old data. Delete the SQLite file to remove all state.
- **Multi-replica:** Each replica maintains its own state DB and qualification lifecycle. No fleet-wide coordination.

See [docs/deployment.md](docs/deployment.md), [docs/security-data-handling.md](docs/security-data-handling.md), and [docs/pii-guidance.md](docs/pii-guidance.md).

---

## Documentation

- [Architecture](docs/architecture.md) — how the Decision Engine works internally
- [Concepts](docs/concepts.md) — DecisionSites, Fast Paths, deoptimization, verification
- [CLI Reference](docs/cli.md) — command-line interface
- [Integration Guide](docs/integration.md) — connecting Ink to your agent
- [Discovery & Trace Format](docs/discovery.md) — trace format, `ink discover`, ROI estimation
- [Claims Registry](docs/claims.md) — every claim with its evidence and scope
- [Production Safety](docs/production-safety.md) — qualification lifecycle, drift detection, failure modes
- [Security & Data Handling](docs/security-data-handling.md) — what's stored, what leaves the machine
- [PII Guidance](docs/pii-guidance.md) — keeping decision state minimal
- [Threat Model](docs/threat-model.md) — security threat model and mitigations
- [Deployment](docs/deployment.md) — single-process, containerized, multi-replica
- [Value Model](docs/value-model.md) — evaluating Ink's impact on your workload
- [Alternatives Comparison](docs/alternatives.md) — how Ink compares to caching, routing, and classifiers
- [Platform Compatibility](docs/compatibility.md) — supported platforms and engines
- [Vision](docs/vision.md) — future direction

## Tests

```bash
pytest python/ink/tests/
```

## License

[Apache-2.0](LICENSE). See [NOTICE](NOTICE) for attribution.

Ink is open source. The SDK, runtime, decision engine, local model, and Fast Path lifecycle are all included.
