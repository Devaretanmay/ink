<p align="center">
  <img src="docs/assets/ink-banner.png" alt="Ink - Behavior JIT for production AI systems" width="100%">
</p>

# Ink

**Stop paying frontier models to make decisions they already solved yesterday.**

Production AI agents call GPT-4o, Claude, or Gemini hundreds of times an hour just to pick a tool, route a customer ticket, or choose a workflow branch. That is slow, expensive, and kind of ridiculous.

Ink watches your model work, records whether the choice actually succeeded, and compiles proven behavior into local Python code that runs in **0.1 milliseconds for $0.00**.

When your agent hits something weird or novel, it calls your model like normal. When it sees something it has already proven 500 times, Ink runs it locally. No cloud. No background daemons. Just a local SQLite file and fast math.

> **Models handle novelty. Ink turns proven behavior into software.**

---

## Why you need this

Every production AI system hits the same wall:

1. **You are paying rent on solved problems.** 70% of your agent's LLM calls aren't creative writing. They are bounded choices: choosing `search_database` vs `escalate_human`, picking a support queue, or checking an approval gate.
2. **Caches don't work for decisions.** Redis saves whatever your model blurted out. If the model hallucinated, a cache faithfully replays the hallucination. A cache can't tell you if an answer was right, and it can't generalize when someone types "billing inquiry" instead of "billing question".
3. **Training a small classifier is a pain in production.** A standalone classifier has no idea when to abstain. It will happily make a wild guess on novel input and crash your workflow. It also won't tell you when your data drifted.

Ink solves this by acting as a **Behavior JIT (Just-In-Time compiler)**. It sits right in front of your model call, learns from real-world outcomes, verifies behavior with statistical confidence intervals, and automatically hands traffic back to your model the moment anything drifts.

---

## Install

```bash
pip install ink-jit
```

*Requires Python 3.11, 3.12, or 3.13 on macOS (with Apple Silicon Metal acceleration) or Linux. Zero cloud dependencies.*

---

## Quickstart (takes 2 minutes)

Take any place in your code where an LLM picks from a list of choices. Wrap it with `ink.wrap`:

```python
from ink import Ink

# Everything saves to a local SQLite database. No background servers required.
ink = Ink(".ink/decisions.db")

@ink.wrap("triage_ticket", choices=["billing", "technical", "sales"])
def route_ticket(ticket_text: str):
    # Your normal slow, expensive model call lives here:
    return call_expensive_llm(ticket_text)

# First 100 times: Calls your model, watches what happens.
# Next 10,000 times: Runs locally in 0.08ms. You pay $0.00.
result = route_ticket("Where can I find my March invoice?")
print(result.choice)  # -> "billing"
print(result.source)  # -> "fallback" (initially) or "fast_path" (once qualified)

# Feed back real-world proof after the downstream action completes:
ink.record_outcome(
    decision_id=result.decision_id,
    quality=1.0,  # 1.0 = success, 0.0 = error
    verifier="zendesk_routing_signal",
    verifier_version="v1",
)
```

---

## See it in action: The Local Console

Ink comes with a built-in, dark-mode halftone console that runs entirely on localhost:

```bash
ink ui
```

Open `http://localhost:4242` to inspect your decisions, check your fast-path hit rate, watch token dollars saved, and inspect historical predictions in memory without side effects:

- **100% offline & private:** No data leaves your machine. No accounts, no cookies, no tracking.
- **Decision explorer:** Search past inputs, see whether they hit the fast path or fell back to your model, and inspect the raw state payload.
- **Drift monitor:** See live agreement rates between your local code and your frontier model.
- **Governed serving controls:** Inspect live serving modes (`ACTIVE`, `CANARY`, `SHADOW`, and `HOST_OVERRIDE`).

---

## The JIT lifecycle

Ink is not a model and not a cache. Ink is an authority lifecycle:

```text
       ┌───────────┐
       │  OBSERVE  │  Requests run through your model; Ink logs states & verified outcomes
       └─────┬─────┘
             │ Minimum evidence threshold reached
             ▼
       ┌───────────┐
       │  QUALIFY  │  Statistical gate tests candidate against error budget (Wilson 95% LB)
       └─────┬─────┘
             │ Passed statistical holdout
             ▼
       ┌───────────┐
       │  COMPILE  │  Builds local Fast Path (Exact hash table or Linear boundary)
       └─────┬─────┘
             │
             ▼
       ┌───────────┐
       │   SERVE   │  Qualified requests run locally (< 0.1ms); novel requests route to model
       └─────┬─────┘
             │
             ▼
       ┌───────────┐
       │  VERIFY   │  Background comparison audits drift; automatically demotes if errors rise
       └───────────┘
```

1. **Observe:** Initial traffic routes 100% to your model. Ink records inputs, decisions, and real-world outcomes in local SQLite storage.
2. **Qualify:** Ink tests candidate behavior on independent holdout data. A candidate must satisfy your site's error budget under a **95% Wilson confidence lower bound**.
3. **Compile:** Ink trains a local execution artifact (`ExactEngine` hash table or `LinearClassifierEngine`).
4. **Serve:** Qualified requests execute locally in sub-milliseconds with zero token bills. Unfamiliar or novel requests automatically route to your model.
5. **Verify:** Ink sends a small sample of live traffic to your model in parallel to check for drift. If your API schema or customer behavior changes and errors rise, Ink immediately revokes local authority back to your model.

---

## What is a DecisionSite?

A **DecisionSite** is a bounded contract in your application code. It defines a single decision point where an AI system selects from a discrete, enumerable set of choices:

```python
from ink import DecisionSite, Ink

site = DecisionSite(
    name="agent.tool_choice",
    choices=("search_kb", "query_database", "escalate_human"),
    state_schema={"task_type": "string", "retries": "integer"},
    local_error_budget=0.01,  # Require 99% accuracy before local serving is allowed
)
```

Every DecisionSite declares:
- **`name`**: Unique identifier for the decision boundary (`agent.tool_choice`, `support.triage`).
- **`choices`**: Tuple of valid output choices.
- **`state_schema`**: Expected input types.
- **`local_error_budget`**: Maximum tolerable error rate (e.g. `0.01` for 1%, `0.05` for 5%).

---

## When to use Ink (and when NOT to)

We want you to use Ink where it works wonders, and skip it where it doesn't make sense.

### Great fits for Ink:
- **Tool selection:** Deciding whether an agent should call `search_docs`, `query_sql`, or `ask_human`.
- **Workflow branching:** The conditional `if/else` edges in LangGraph, PydanticAI, or custom state machines.
- **Support & ticket triage:** Directing 50,000 inbound tickets a day into 8 known department queues.
- **Approval & risk gates:** Deciding whether a user action needs manual review, extra auth, or instant clearance.
- **Incident routing:** Paging the right on-call engineer based on error codes and telemetry summaries.

### Terrible fits for Ink:
- **Open-ended creative writing:** Drafting emails, writing essays, or generating marketing copy. Keep calling Claude.
- **Unbounded outputs:** Extracting arbitrary JSON structures with 50 fields or generating raw code snippets.
- **Workflows with zero feedback:** If you have no way to verify whether an action worked or failed, Ink will refuse to grant local authority. Ink doesn't guess; it needs proof.

---

## Why Ink is not just a cache

A cache stores outputs and blindly replays them whenever an input matches.

- **Caches repeat mistakes.** If your model hallucinated yesterday, a cache repeats that hallucination today at lightning speed.
- **Caches can't generalize.** If a user writes "invoice help" instead of "billing help", a cache misses.
- **Caches are blind to drift.** If your downstream database column changes, a cache will happily keep serving invalid queries until your logs catch fire.

Ink requires independent outcome verification before candidate behavior can qualify. Ink serves through compiled mathematical execution boundaries, not raw string matching, and audits active paths continuously.

---

## Why Ink is not "just train a classifier"

A standalone classifier predicts on every input, even when an input is totally out-of-distribution. It has no concept of when to abstain, provides no fail-open fallback to a frontier model, and cannot quantify risk against an explicit statistical error budget.

**Prediction and permission are different things.** A classifier predicts; Ink manages whether local behavior has earned the authority to serve live traffic.

---

## Architecture: The 4 planes

Ink cleanly separates synchronous serving from background compilation:

```text
1. SERVING PLANE      Qualified Local Fast Path (< 0.10ms)  │  Host Fallback on Novelty
─────────────────────────────────────────────────────────────────────────────────────────
2. EVIDENCE PLANE     Local SQLite Ledger (WAL Mode)  •  Verified Outcomes  •  Lineage
─────────────────────────────────────────────────────────────────────────────────────────
3. COMPILER PLANE     Background Representation Search  •  Exact & Linear Training
─────────────────────────────────────────────────────────────────────────────────────────
4. AUTHORITY PLANE    Wilson 95% Confidence Gate  •  Error Budgets  •  Drift Revocation
```

- **Synchronous Serving:** Runs in-process on CPU or Apple Silicon Metal. Fast Paths resolve in `< 0.01 ms` for Exact matches and `< 0.10 ms` for Linear boundaries.
- **Fail-Open Invariant:** If the database locks or an unexpected error occurs, Ink immediately routes to your model fallback. Your app never crashes because of Ink.
- **Zero Direct Neural Serving:** Neural policy models (`ink-decision-small`) operate exclusively in the background compiler plane. They never intercept live production traffic.

---

## Production benchmarks

Controlled benchmark results across canonical workloads (evaluated on Apple Silicon and Linux CPU):

| Workload | Serving Engine | Evaluated Traffic | Local Fast-Path Rate | Holdout Error Rate | 95% Wilson Lower Bound | Required Budget | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Tool Routing** | Linear | 319 requests | **78.4%** | **0.0%** | **98.49%** | $\le 2.0\%$ | **ACTIVE** |
| **Support Triage** | Exact | 300 requests | **42.3%** | **0.0%** | **97.06%** | $\le 5.0\%$ | **ACTIVE** |
| **Incident Triage** | Exact | 300 requests | **55.6%** | **0.0%** | **97.75%** | $\le 5.0\%$ | **ACTIVE** |

*Measured in-process on local host CPU / Apple Silicon Metal benchmarks. ExactEngine latency: $< 0.01\text{ ms}$; LinearClassifierEngine latency: $< 0.10\text{ ms}$.*

---

## CLI at a glance

```bash
# Launch the local halftone dashboard on http://localhost:4242
ink ui

# Scan existing LLM traces to find repeated, compilable decisions
ink discover traces.jsonl --profile

# Inspect accumulated evidence and Wilson confidence for a site
ink inspect agent.tool_choice

# Re-evaluate shadow candidates and demote drifted paths
ink maintenance

# Verify database health and runtime integrity
ink doctor
```

---

## Documentation

- [Getting Started](docs/getting-started.md) — 2-minute setup and walkthrough
- [Why Ink](docs/why-ink.md) — Category breakdown vs caches, classifiers, and gateways
- [Decision Audit](docs/decision-audit.md) — How to evaluate your first decision site
- [Core Concepts](docs/concepts.md) — DecisionSites, Host fallbacks, outcomes, and Fast Paths
- [Architecture](docs/architecture.md) — Deep dive into the 4 planes
- [Production Safety](docs/production-safety.md) — Wilson bounds, error budgets, and drift deoptimization
- [Integration](docs/integration.md) — LangGraph, PydanticAI, and async adapters
- [Deployment](docs/deployment.md) — SQLite WAL concurrency, worker processes, and fleet setups
- [Benchmarks](docs/benchmarks.md) — Evidence hierarchy and reproducible benchmark suites
- [CLI Reference](docs/cli.md) — Complete command-line manual

---

## License

Released under the [Apache-2.0 License](LICENSE).
