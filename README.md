# Ink

**Behavior JIT for Production AI.**

> Models handle novelty. Ink turns proven behavior into software.

```text
new / uncertain state
        ↓
   host model decides
        ↓
 Ink observes decision + outcome
        ↓
 repeated behavior emerges
        ↓
 candidate local behavior
        ↓
       shadow
        ↓
 independently verified outcomes
        ↓
   qualify (Hoeffding concentration bound)
        ↓
   local Fast Path (sub-millisecond, $0)
        ↓
 keep comparing against reality
        ↓
 drift? → revoke Fast Path → model again
```

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-production--ready-brightgreen.svg)]()

---

## 1. What is Ink?

Ink is a **Behavior JIT (Just-In-Time compiler) for bounded semantic decisions in production AI systems**.

Just as a JavaScript or Java JIT engine identifies hot bytecode loops and compiles them down to native machine code at runtime, Ink identifies hot semantic decisions in your AI application (routing, triage, tool selection, escalation, classification) and turns them into locally executed **Fast Paths**.

Once a decision is qualified, it executes in sub-millisecond local software with **zero LLM API calls and zero cloud latency**. Novel or uncertain inputs automatically fall through to your host model.

## 2. What problem does it solve?

Production AI systems make thousands of repetitive, bounded decisions every day:
- *"Does this error require escalation?"*
- *"Which worker agent should handle this subtask?"*
- *"Is this inquiry billing, technical, or account management?"*
- *"Can this refund request be auto-approved?"*

Today, teams pay frontier LLMs ($0.003–$0.03 per call, 400ms–2000ms latency) to make the exact same decision over and over, even after the behavior has become completely deterministic.

Hand-coding static regex or heuristic rules is brittle and breaks. Semantic caches serve stale or incorrect outputs when distribution shifts. Ink solves this by **learning candidate behavior from production traffic and compiling local software only when backed by mathematical evidence**.

## 3. What is the mental model?

- **Confidence does not grant authority.** A model being "99% confident" means nothing in production.
- **Similarity does not grant authority.** Two sentences having cosine similarity 0.94 does not prove the same business action should be taken.
- **Evidence grants authority.** A Fast Path is only granted execution authority when independent real-world outcomes prove that local execution matches host model quality with statistical certainty.

When a decision boundary is cold, your host model explores in pencil. As verified evidence accumulates, Ink darkens the path. When behavior proves itself, Ink **commits the line in software**. If reality drifts, Ink revokes authority and falls back to your model immediately.

## 4. How is this different from semantic caching?

| Dimension | Semantic Caching | Ink (Behavior JIT) |
| :--- | :--- | :--- |
| **Authority Mechanism** | Cosine distance / vector similarity | Independent ground-truth outcome verification |
| **Drift Detection** | None (silently serves stale data) | Continuous comparison traffic + automated deoptimization |
| **Execution Tier** | Vector DB query (~15-50ms) | Local memory index / linear boundary (< 1ms) |
| **Ambiguity Handling** | Returns nearest neighbor | Explicit abstention on multi-intent boundary margins |
| **Safety Invariant** | Heuristic threshold | Nonparametric concentration bounds (Hoeffding) |

## 5. How is this different from prompt distillation?

Prompt distillation trains a smaller model offline from synthetically generated teacher tokens. It requires batch training jobs, manual redeployment cycles, and cannot adapt as production traffic shifts.

Ink runs **online in your live runtime**. It compiles local execution engines progressively, qualifies them against live business outcomes, and deoptimizes in real time if distributions change.

## 6. How is this different from fine-tuning?

Fine-tuning modifies neural network weights to predict tokens. It does not provide:
1. Serving authority boundaries (a fine-tuned model will still hallucinate on out-of-distribution inputs).
2. Cost elimination (you still pay for model compute per token).
3. Zero-shot deoptimization when underlying business logic changes.

Ink wraps *around* any model (frontier or fine-tuned) and replaces model invocation with deterministic software whenever evidence allows.

---

## 7. How does a developer integrate it?

Install Ink:

```bash
pip install ink-jit
```

### Option A: The `@ink.wrap` Decorator (Recommended)

Wrap any existing decision function:

```python
from ink import DecisionSite, Ink

ink = Ink()
site = DecisionSite(
    name="workflow.route",
    state_schema={"tier": "string", "amount": "number"},
    choices=("auto_approve", "manual_review"),
)

@ink.wrap(site)
def route_transaction(state: dict) -> str:
    # Your original LLM call — only executed when Ink falls back
    return call_frontier_model(state)

# In your application:
decision = route_transaction({"tier": "enterprise", "amount": 150.0})
print(decision.choice)  # e.g., 'auto_approve'
```

### Option B: The Explicit `ink.decide` API

```python
from ink import DecisionSite, Ink, Outcome

ink = Ink()
site = DecisionSite(
    name="ticket.triage",
    state_schema={"subject": "string", "priority": "string"},
    choices=("billing", "tech", "general"),
)

# 1. Decide: Serves via Fast Path if qualified, falls back to model if novel
res = ink.decide(
    site=site,
    state={"subject": "Billing issue", "priority": "high"},
    fallback=lambda: call_frontier_model(...),
)

# 2. Record independent outcome when downstream result is known
ink.record_outcome(
    res.decision_id,
    Outcome(quality=1.0, verifier="support_audit", verifier_version="v1"),
)
```

---

## 8. How does qualification work? (The Math, Simplified)

Ink never promotes a Fast Path based on training loss or heuristics. Promotion requires passing **Hoeffding's Inequality** on independent holdout and shadow evaluation partitions:

$$\mathbb{P}(\mu \le \hat{\mu} - \epsilon) \le e^{-2n\epsilon^2} \le \alpha$$

Given $n$ independently verified observations with empirical quality $\hat{\mu}$, the conservative statistical lower bound is:

$$\text{Lower Bound} = \hat{\mu} - \sqrt{\frac{\ln(1/\alpha)}{2n}}$$

At standard risk parameter $\alpha = 0.05$ ($\ln(20) \approx 2.996$), Ink guarantees with $95\%$ confidence that true production quality meets or exceeds your required threshold before granting local execution authority.

---

## 9. How does safety and drift work?

1. **Continuous Comparison Traffic:** Even when a site is `ACTIVE`, Ink samples a small percentage of requests (default 5–25%) to run both the local Fast Path and the host fallback in parallel.
2. **Automated Demotion (Deopt):** If the comparison arm detects that local quality drops below the model quality by more than `max_degradation`, Ink immediately **revokes the Fast Path** and transitions the site back to host fallback.
3. **Out-of-Distribution Rejection:** States outside trained coverage regions or near ambiguous decision boundaries automatically abstain and route to the model.
4. **Fail-Open Resilience:** If SQLite encounters a disk lock or transient failure, Ink falls open to the host model without interrupting application flow.

---

## 10. Developer CLI

Ink includes a complete developer CLI for inspecting, evaluating, and managing local decision boundaries:

```bash
# View summary table of registered sites, states, and serving rates
ink status

# Output machine-readable JSON status
ink status --json

# Run system health diagnostics (DB integrity, schema, available engines)
ink doctor

# Discover candidate decision boundaries from JSONL application traces
ink discover traces.jsonl

# Inspect deep state coverage and active semantic regions for a site
ink inspect workflow.route

# Launch local-first developer console
ink console
```

---

## 11. Ink Console Alpha

Ink includes a local-first web dashboard that runs entirely on your machine with **zero external cloud accounts and zero CDN dependencies**:

```bash
ink console --port 8000
```

Open `http://127.0.0.1:8000` to inspect:
- **Overview:** Live decision volume, Fast Path serving percentage, estimated latency and cost savings.
- **DecisionSites:** Full registry of sites, schemas, lifecycle status (`OBSERVE`, `SHADOW`, `ACTIVE`).
- **Inks & Fast Paths:** Compiled engines, active regions, and qualification certificates.
- **Activity Feed:** Live streaming ledger of recent decisions with serving source and confidence.
- **Doctor & System:** Database integrity, SQLite PRAGMA version, and engine backends.

Try the built-in demo mode:
```bash
ink console --demo
```

---

## 12. Workload Fit Guide

### ✅ Ideal Workloads (High ROI)
- **Workflow & Ticket Routing:** Deterministic or natural-language triage nodes handling $> 1,000$ decisions/day.
- **Agent Graph Supervisors:** LangGraph, CrewAI, or AutoGen orchestrator nodes deciding next steps or worker delegation.
- **Tool Selection:** Selecting which API tool or SQL query to run based on structured request state.
- **Risk & Policy Gates:** Fast-approval and compliance triage where verifiable ground truth is recorded downstream.

### ❌ Poor Workloads (Do Not Use)
- **Open-Ended Text Generation:** Generating essays, poems, codebases, or conversational dialogue.
- **Unbounded State Spaces:** Systems where every request is completely unique with zero repeated semantic intent.
- **Zero Feedback Systems:** Workloads where outcomes can never be verified or ground truth is impossible to determine.

---

## 13. Running the Reference Examples

The repository includes ready-to-run reference examples demonstrating all core capabilities:

```bash
# 5-minute quickstart
python examples/quickstart.py

# Reference Example A: Structured transaction routing (ExactEngine)
python examples/structured_routing.py

# Reference Example B: Semantic NLP ticket triage (LinearClassifierEngine)
python examples/semantic_triage.py

# Reference Example C: Agent graph supervisor (LangGraph style)
python examples/agent_supervisor.py

# Framework Adapter A: LangGraph conditional edge wrapper
python examples/framework_langgraph.py

# Framework Adapter B: PydanticAI agent tool wrapper
python examples/framework_pydantic_ai.py

# Real model integration with offline simulation fallback
python examples/real_model_api.py

# Generate design partner pilot evaluation report
python examples/pilot_report.py
```

---

## License

Ink is licensed under the Apache 2.0 License. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for details.
