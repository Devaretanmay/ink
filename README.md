<p align="center">
  <img src="docs/assets/ink-banner.png" alt="Ink - Behavior JIT for production AI systems" width="100%">
</p>

# Ink

**Behavior JIT for production AI systems.**

Production AI systems accumulate behavioral knowledge over time. Once that behavior is independently proven, it should become software instead of being rented repeatedly from a general-purpose model.

> **Models handle novelty. Ink turns proven behavior into software.**

<p align="center">
  <video src="docs/assets/ink-launch.mp4" controls="controls" width="100%"></video>
  <br>
  <a href="docs/assets/ink-launch.mp4"><strong>▶ Watch the Launch Video</strong></a>
</p>


---

## What is Ink?

Ink is an embedded local runtime that observes repeated, bounded AI decisions, records verified outcomes, and compiles qualified behavior into sub-millisecond local software.

When an AI system operates in production, it repeatedly calls frontier models for bounded choices: tool selection, ticket triage, approval gates, and workflow branches. Ink turns that proven behavior into owned software.

**Your AI should not pay to rethink what it already knows.**

---

## Install

```bash
pip install ink-jit
```

*Requires Python 3.11, 3.12, or 3.13 on Linux or macOS.*

---

## Minimal Real Example

Wrap your existing model call in an explicit `DecisionSite`. Pass your existing model as the fallback:

```python
from ink import DecisionSite, Ink

# 1. Initialize local evidence storage
ink = Ink(".ink/decisions.db")

# 2. Define a bounded DecisionSite
site = DecisionSite(
    name="agent.tool_choice",
    choices=("search_kb", "query_db", "escalate_human"),
    state_schema={"task_type": "string"},
)
ink.register(site)

# 3. Resolve decisions: serves locally (< 0.1ms) when qualified; falls back to Host when novel
state = {"task_type": "account_balance"}
result = ink.decide(
    site=site,
    state=state,
    fallback=lambda: call_llm(state),
)

# 4. Record verified outcome after downstream action completes
ink.record_outcome(
    decision_id=result.decision_id,
    quality=1.0,  # 1.0 = verified success, 0.0 = error
    verifier="api_execution_signal",
    verifier_version="v1",
)
```

---

## The JIT Lifecycle

Ink is not a model and not a cache. Ink is an authority lifecycle:

```text
       ┌───────────┐
       │  OBSERVE  │  Requests execute through your Host model; Ink records states and outcomes
       └─────┬─────┘
             │
             ▼
       ┌───────────┐
       │  QUALIFY  │  Independent statistical gate evaluates candidates against site error budget
       └─────┬─────┘
             │
             ▼
       ┌───────────┐
       │  COMPILE  │  Compiles local execution artifacts (Exact hash tables or Linear boundaries)
       └─────┬─────┘
             │
             ▼
       ┌───────────┐
       │   SERVE   │  Qualified requests execute locally (< 0.1ms); novel requests route to Host
       └─────┬─────┘
             │
             ▼
       ┌───────────┐
       │  VERIFY   │  Continuous comparison traffic detects drift; revokes authority if errors rise
       └───────────┘
```

1. **Observe**: New requests execute through your Host model. Ink records inputs, decisions, and real outcomes in local SQLite storage.
2. **Qualify**: Ink evaluates candidate behavior on independent holdout data. A candidate must satisfy the site error budget under a 95% Wilson confidence lower bound.
3. **Compile**: Ink trains a local execution artifact (`ExactEngine` or `LinearClassifierEngine`).
4. **Serve**: Qualified candidates earn serving authority and execute locally in sub-milliseconds without network or token costs.
5. **Verify**: Live comparison traffic continuously audits active Fast Paths. When reevaluation detects that error rates exceed the budget, Ink revokes serving authority back to the Host model.

---

## What is a DecisionSite?

A **DecisionSite** is a bounded contract in your application code. It specifies a single decision point where an AI system selects from a discrete, enumerable set of choices.

Every DecisionSite declares:
- **`name`**: Unique hierarchical boundary name (`agent.tool_choice`, `support.triage`).
- **`choices`**: Tuple of valid output choices.
- **`state_schema`**: Expected input types.

Ink optimizes bounded choices. Open-ended generation (such as creative prose, open dialogue, or arbitrary text extraction) stays with the Host model.

---

## Workload Fit

### Good DecisionSites
- **Tool Selection**: Choosing which tool or API to invoke from an agent registry.
- **Support & Ticket Triage**: Directing customer requests to specialist queues.
- **Approval & Security Gates**: Deciding whether to allow, review, or block operations.
- **Workflow Branching**: Conditional transitions in agent state machines (LangGraph, PydanticAI).
- **Incident Escalation**: Paging engineers based on error codes and telemetry summaries.

### Poor DecisionSites
- **Open-Ended Writing**: Drafting emails, essays, or conversational answers.
- **Unbounded Arguments**: Extracting arbitrary code or raw unstructured text.
- **Zero-Feedback Workflows**: Tasks where outcomes cannot be independently measured or verified.

---

## Why Ink is Not a Cache

A cache stores outputs and replays them when inputs match.

Caches do not know if a previous answer was correct. If your model made a mistake yesterday, a cache repeats that mistake today. Caches cannot generalize across structured state variations, enforce statistical error budgets, or detect distribution shift.

Ink requires independent outcome verification before candidate behavior can qualify. Ink serves through compiled mathematical execution boundaries, not raw memory retrieval.

---

## Why Ink is Not "Just a Classifier"

A classifier is a model that always makes a prediction.

A standalone classifier cannot:
- Decide when an input is sufficiently familiar to serve safely.
- Abstain autonomously on out-of-distribution or ambiguous requests.
- Guarantee an explicit statistical error budget on live production traffic.
- Fall open to a frontier Host model when it encounters novelty.
- Demote itself when downstream business rules change.

**Prediction and permission are different things.** A classifier predicts; Ink manages whether local behavior is permitted to serve.

---

## Why Ink is Not Model Routing

Model gateways route traffic between different external model providers (e.g., routing between GPT-4o and GPT-4o-mini).

Model routing still pays external providers for every token and still incurs network round-trips. It optimizes rental rates rather than building owned software assets.

Ink turns proven decisions into owned, local code. Novel requests go to your Host model; proven behavior stays in your software.

---

## Architecture

Ink separates synchronous serving from background compilation across four planes:

```text
1. SERVING PLANE      Qualified Local Fast Path (< 0.10ms)  │  Host Fallback on Novelty
─────────────────────────────────────────────────────────────────────────────────────────
2. EVIDENCE PLANE     Local SQLite Ledger (WAL Mode)  •  Verified Outcomes  •  Lineage
─────────────────────────────────────────────────────────────────────────────────────────
3. COMPILER PLANE     Background Representation Competition  •  Exact & Linear Training
─────────────────────────────────────────────────────────────────────────────────────────
4. AUTHORITY PLANE    Wilson 95% Confidence Gate  •  Error Budgets  •  Drift Revocation
```

- **Synchronous Serving**: Runs in-process on CPU or Apple Silicon Metal. Fast Paths resolve in sub-milliseconds (typically $< 0.01\text{ ms}$ for Exact, $< 0.10\text{ ms}$ for Linear on local benchmarks) with zero network overhead.
- **Fail-Open Invariant**: Ink routes to the Host fallback when supported runtime or storage failures prevent local execution.
- **Zero Direct Neural Serving**: Neural models (`ink-decision-small`) operate exclusively in the background compiler plane. They never intercept live production traffic directly.

---

## Safety & Serving Authority

- **Confidence does not grant authority.** High softmax confidence is not proof of correctness.
- **Similarity does not grant authority.** Vector proximity does not guarantee safety.
- **Model identity does not grant authority.** Neural representations are proposals, not permission.
- **Authority requires evidence.** A candidate must clear the Wilson 95% confidence lower bound against the site error budget:

$$\text{Wilson Lower Bound}(k, n) \ge 1.0 - \text{Error Budget}$$

- **Continuous Verification**: Live comparison traffic continuously audits active Fast Paths. When reevaluation detects that error rates exceed the budget, serving authority is revoked back to the Host model.

---

## Start with a Decision Audit

The fastest path to evaluating Ink is a **Decision Audit**:

> **Give us one high-volume bounded model decision. We will determine whether any portion of it has a defensible path toward local serving authority.**

An audit examines decision definition, state schema, choice sets, production volume, outcome provenance, error budgets, and candidate coverage.

A negative result is a valid outcome. If an audit reveals that a workload cannot satisfy statistical safety contracts, Ink explicitly advises against local compilation. We never weaken qualification thresholds to manufacture a positive pilot.

Read the [Decision Audit Guide](docs/decision-audit.md) to evaluate your first decision site.

---

## Documentation

- [Getting Started](docs/getting-started.md) — 5-minute quickstart and workflow walkthrough
- [Why Ink](docs/why-ink.md) — Category comparison with caches, classifiers, and fine-tuning
- [Decision Audit](docs/decision-audit.md) — The 15-point audit methodology for candidate workloads
- [Concepts](docs/concepts.md) — DecisionSites, Host fallback, outcome provenance, and Fast Paths
- [Architecture](docs/architecture.md) — The four planes: Serving, Evidence, Compiler, and Authority
- [Production Safety](docs/production-safety.md) — Statistical qualification, Wilson confidence bounds, and drift
- [Integration](docs/integration.md) — LangGraph, PydanticAI, and asynchronous runtime adapters
- [Deployment](docs/deployment.md) — Embedded SQLite storage, WAL concurrency, and fleet patterns
- [Benchmarks](docs/benchmarks.md) — Evidence hierarchy, controlled workload results, and reproduction
- [CLI Reference](docs/cli.md) — Local discovery, inspection, compilation, and diagnostics

---

## Project Status & License

Ink is under active development. The runtime, embedded SQLite evidence ledger, and statistical qualification system have completed internal controlled validation (Level 3 evidence). External design-partner validation (Level 4) is currently in progress.

Released under the [Apache-2.0 License](LICENSE).
