# Ink

**Behavior JIT for production AI.**

> Models handle novelty. Ink turns proven behavior into software.

Your AI shouldn't think twice about what it already knows. Ink is a local runtime for
production AI teams whose agents make the same **bounded, verifiable decisions** over and
over — routing, triage, escalation, tool selection, classification. Ink observes those
decisions and their real outcomes, proves which behavior has become predictable **and**
correct, and serves that proven behavior locally as a **Fast Path**. Novel or uncertain
states keep going to your model. When reality changes, Ink revokes the Fast Path.

A model explores in pencil. Repeated evidence darkens the path. When behavior proves
itself, **commit the line in Ink.**

```text
observe → candidate → shadow → qualify → active → compare → deopt (on drift)
```

---

## The problem

Your AI system keeps paying a remote model to reconsider behavior it has effectively
already learned. The same support ticket gets routed the same way ten thousand times, at
full latency and full price, even though the answer stopped being a decision long ago.

The default choice — do nothing and call the model forever — is the most expensive one.
But naively caching responses is dangerous: a cache stores *similarity*, and when policy
or reality drifts it silently keeps serving stale decisions with no way to notice.

Ink exists to remove **unnecessary inference** without removing correctness.

## What Ink does

- **Observes** decisions and their independent outcomes as your agent runs normally.
- **Shadows** candidate behavior alongside your model, comparing outcomes before anything is served.
- **Qualifies** a Fast Path only when statistical evidence shows it matches model quality.
- **Serves** proven behavior locally, with zero network calls during inference.
- **Deoptimizes** automatically — ongoing comparison traffic detects drift and revokes the Fast Path.

## Why Ink is different

Serving authority in Ink comes from **verified outcomes**, not similarity or confidence alone.

| Often confused with | What it does | How Ink differs |
| :--- | :--- | :--- |
| **Semantic cache** | Reuses responses to *similar* prior inputs | Ink never serves on similarity. It requires independent outcome evidence first. |
| **Model router** | Chooses which model handles a request | Every router path still calls a model. Ink can eliminate the call entirely. |
| **Cheaper model** | Swaps in a smaller model | A smaller model still reasons every time. Ink serves proven behavior. |
| **Hard-coded rules** | Hand-authored if/else logic | Ink learns candidate behavior from observed model decisions; no rule authoring. |
| **Fine-tuned classifier** | Trains a specialty model offline | Ink qualifies continuously in production and deoptimizes when reality drifts. |
| **LLM gateway** | Proxies and manages API calls | Ink runs locally with zero network calls during inference. |

**Caching** asks "have I seen something similar?" **Routing** asks "which model should
answer?" **Ink** asks "has this exact behavior been proven correct by real outcomes, and is
it still true today?" — and only then serves it locally.

---

## Install

```bash
pip install ink-jit
```

The import is `import ink`; the CLI is `ink`. The distribution bundles the Ink runtime,
including the local decision model used during qualification. Supported: Python 3.11–3.13
on macOS (Apple Silicon) and Linux.

## Evaluate in minutes with `ink discover`

Point discovery at traces you already have. It changes no production code and tells you
where repeated behavior exists — before you integrate anything.

```bash
ink discover traces.jsonl
```

```text
Found 3 candidate call sites.

1. support.route
   traffic: 1,500/day (25 observed)
   repetition: 24.0%
   choices: 3 ['refund', 'request_info', 'specialist']
   verifier readiness: VERIFIER_READY (100.0% coverage)
   model latency: 178.0ms
   recommendation: STRONG CANDIDATE
   reason: Strong candidate: 24.0% repetition, bounded choices (3), break-even in ~658 decisions.

2. agent.tool_select
   ...recommendation: INVESTIGATE

3. response.generate
   ...recommendation: INVESTIGATE
```

`ink discover` is the front door. If your workload is mostly open-ended generation, it will
say so — that is the point. Add `--snippet` for ready-to-paste integration code and
`--profile` for economic detail. Trace format: [docs/discovery.md](docs/discovery.md).
Sample data: [`examples/discovery/sample_traces.jsonl`](examples/discovery/sample_traces.jsonl).

## Integrate one DecisionSite

A **DecisionSite** is one bounded choice in your agent. Wrap it once, keep your model as
the fallback, and report the real outcome so Ink can prove the decision.

```python
from ink import DecisionSite, Ink, FallbackResult

site = DecisionSite(
    name="support.route",
    state_schema={"text": "string", "amount": "integer"},
    choices=("refund", "request_info", "specialist"),
)

with Ink() as engine:
    engine.register(site)

    # Serves locally once qualified; calls your model otherwise.
    result = engine.decide(
        site=site,
        state={"text": "Item damaged in shipping", "amount": 25},
        fallback=lambda: FallbackResult(my_llm_call(), cost=0.002, model_calls=1),
    )

    receipt = execute_action(result.choice)

    # The independent outcome is what authorizes the Fast Path.
    engine.record_outcome(
        result.decision_id,
        quality=1.0 if receipt.success else 0.0,
        verifier="fulfillment_system",
        verifier_version="1",
        evidence={"order_id": receipt.order_id},
    )
```

```bash
ink sites  --db .ink/decisions.db   # registered sites
ink status support.route --db .ink/decisions.db
ink value  --db .ink/decisions.db   # calls avoided, latency saved
```

## How qualification and Fast Paths work

A local model being confident is **not** enough to serve a decision. Ink separates
*candidate generation* from *serving authority*. Nothing serves live traffic until it has
passed shadow qualification against independent outcomes.

```text
OBSERVE    Your agent runs normally. Ink records decisions and outcomes.
    ↓
CANDIDATE  Ink estimates repetition, entropy, and qualification cost.
    ↓
SHADOW     The candidate runs alongside the model. Outcomes are compared.
    ↓
QUALIFY    Statistical tests confirm the Fast Path matches model quality.
    ↓
ACTIVE     Qualified decisions serve locally. Novel states fall back to your model.
    ↓
COMPARE    Ongoing comparison traffic (default 5–35%) monitors for drift.
    ↓
DEOPT      If quality degrades, Ink revokes the Fast Path automatically.
```

**Two serving tiers.** Exact repeated states replay in **< 0.2 ms** with no inference.
Unseen-but-similar states inside a qualified region are served by the bundled local model
in **~48 ms**, still with no remote call. Anything outside qualified coverage goes to your
model.

> **Ink can still serve a wrong decision before drift is detected.** In the controlled
> benchmark, 7–8 wrong serves occurred before demotion — materially safer than static
> caching (12–18% ongoing error under the same conditions), but not zero.

## Measured results

Controlled/synthetic benchmark: 18,000 decisions across 4 workloads with 70/30 temporal
evaluation ([full report](benchmarks/results/competitive_frontier/REPORT.md), [claims
registry](docs/claims.md)).

| Metric | Measured | Scope |
| :--- | :--- | :--- |
| Exact Fast Path latency | 0.18–0.19 ms (p50) | Qualified exact-match local serves only |
| Learned local decision latency | 47.71 ms (p50) | Local model inference, no remote call |
| Remote fallback latency | Provider-dependent | Unqualified decisions → your model |
| DecisionSite call reduction | 19.9–40.4% | Within qualified bounded decision sites |
| Whole-application call reduction | 3.99–10.10% | Entire app (sites ≈ 15–25% of traffic) |
| Wrong serves before demotion | 7–8 | Under injected passive policy drift |
| Static cache wrong-serve rate | 12.0–18.0% | Same drift conditions |
| High-entropy workload | Correctly rejected | Refused compilation (0% wasted resources) |

**Read the denominators.** Bounded verifiable decisions are typically 15–25% of an
application's model calls; whole-application savings reflect that. Exact Fast Path latency
(< 0.2 ms) and learned local latency (~48 ms) are different tiers and are not
interchangeable. Ink does **not** claim zero errors, 80% company-wide savings, replacement
of all model calls, or faster entire applications. See [docs/claims.md](docs/claims.md).

## Why savings come second

Avoided spend is real but rarely the best reason to adopt Ink. The durable benefits are:

- **Latency** — qualified decisions return locally instead of over the network.
- **Locality** — decision state never leaves the process during inference.
- **Provider independence** — qualified paths keep working when a provider is slow or degraded.
- **Rate-limit relief** — avoided calls free headroom in provider quotas.
- **Stale-decision revocation** — drift is detected and the Fast Path is revoked, unlike a static cache.

## Deployment and data handling

- **Storage:** local SQLite at `.ink/decisions.db`. No cloud control plane, no dashboard.
- **Network:** zero network calls during `decide()` and `record_outcome()`. Model weights are
  downloaded once from HuggingFace on first learned use. Run `ink model-install` during
  build/deploy to pre-provision.
- **Retention:** `ink retain --days N` compacts old data; delete the SQLite file to remove all state.
- **Multi-replica:** each replica keeps its own state DB and qualification lifecycle.

See [docs/deployment.md](docs/deployment.md) and
[docs/security-data-handling.md](docs/security-data-handling.md).

## Documentation

- [Discovery & Trace Format](docs/discovery.md) — the trace format and `ink discover`
- [Concepts](docs/concepts.md) — DecisionSites, Fast Paths, verification, deoptimization
- [Architecture](docs/architecture.md) — how the Decision Engine works internally
- [CLI Reference](docs/cli.md) — every command
- [Integration Guide](docs/integration.md) — wiring Ink into your agent
- [Production Safety](docs/production-safety.md) — lifecycle, drift detection, failure modes
- [Security & Data Handling](docs/security-data-handling.md) — what is stored, what leaves the machine
- [Deployment](docs/deployment.md) — single-process, containerized, multi-replica
- [Value Model](docs/value-model.md) — evaluating Ink's impact on your workload
- [Alternatives](docs/alternatives.md) — Ink vs. caching, routing, and classifiers
- [Claims Registry](docs/claims.md) — every claim with its evidence and scope
- [Platform Compatibility](docs/compatibility.md) — supported platforms and engines

## Tests

```bash
pytest python/ink/tests/
```

## License

[Apache-2.0](LICENSE). See [NOTICE](NOTICE) for attribution. Ink is open source: the SDK,
runtime, decision engine, local model, and Fast Path lifecycle are all included.
