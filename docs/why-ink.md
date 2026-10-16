# Why Ink

Production AI systems learn over time. Once that behavior is independently proven, it should become software instead of being rented repeatedly from a general-purpose model.

Ink is the Behavior JIT (Just-In-Time compiler) that turns proven model behavior into owned software.

---

## The core problem

Every day, production AI agents and apps make millions of bounded decisions:
- Selecting which tool to execute from a fixed registry
- Routing incoming customer requests to specialist teams
- Triaging security alerts and incident severity
- Evaluating workflow approval and risk gates

Today, teams pay frontier models repeatedly for these decisions. Every request costs network latency ($200\text{--}1,200\text{ ms}$), token fees, and operational exposure to third-party outages and model changes.

Even after an agent has made the exact same choice 10,000 times with verified success, it still calls the model again.

**Your AI should not pay to rethink what it already knows.**

---

## Prediction vs. permission

The core philosophy of Ink is simple:

> **Prediction and permission are different things.**

A classifier predicts what choice might be correct. A fine-tuned model predicts. A prompt-optimized model predicts.

None of them decide whether local execution has earned the right to serve live traffic. None of them quantify statistical risk against an explicit error budget. None of them automatically revoke their own authority when business reality shifts.

Ink is not a prediction engine. **Ink is an authority lifecycle.**

---

## Ink compared to alternatives

### 1. Caching (exact & semantic)
- **How caching works:** Hashes inputs or performs nearest-neighbor vector search on embeddings. Returns previous outputs on hit.
- **Why it breaks in production:** Caches don't know if a previous answer was right. If your model hallucinated yesterday, a cache repeats that hallucination today at lightning speed. Caches can't generalize across structured state variations, can't enforce statistical error budgets, and have no concept of verified outcomes.
- **The Ink difference:** Ink requires independent outcome verification before candidate behavior can qualify. Ink serves through compiled execution boundaries, not raw string or vector recall.

### 2. Static rules & heuristics
- **How rules work:** Engineers manually write `if/else` statements or regex patterns.
- **Why they break in production:** Natural language and agent state spaces are too high-dimensional for manual rules. Hand-written rules become brittle, conflict with each other, and fail silently as customer behavior changes.
- **The Ink difference:** Ink compiles execution boundaries automatically from observed behavior and verifies them continuously against production outcomes.

### 3. "Just train a classifier"
- **How classifiers work:** An engineer trains a classical model (logistic regression, gradient boosting, or small BERT) on logged outputs.
- **Why they break in production:**
  - Classifiers predict on every input, even when an input is totally out-of-distribution.
  - Classifiers don't know when to abstain.
  - Classifiers can't detect distribution shift autonomously.
  - Classifiers have no fail-open fallback back to your frontier model.
- **The Ink difference:** Ink grants local serving authority only to inputs within qualified coverage regions. Unseen, novel, or ambiguous requests automatically fall back to your model.

### 4. Fine-tuning smaller models
- **How fine-tuning works:** Distills frontier model outputs into a 1B–8B parameter open-weights model hosted on dedicated GPU instances.
- **Why it is incomplete:**
  - Still requires GPU inference infrastructure, cold starts, and hosting costs.
  - Still produces non-deterministic text generation with tail latency ($50\text{--}300\text{ ms}$).
  - Lacks runtime safety contracts: a 3B model can't tell you when its own distribution has drifted.
- **The Ink difference:** Qualified Fast Paths run in sub-milliseconds on CPU or Apple Silicon without GPU infrastructure. When behavior is proven, the cheapest model is no model.

### 5. Model routing
- **How routing works:** A gateway selects whether to call GPT-4o, Claude 3.5 Sonnet, or GPT-4o-mini based on query complexity.
- **Why it is incomplete:** Model routing still pays model providers for every single request. It optimizes rental rates rather than building owned software assets.
- **The Ink difference:** Ink turns proven decisions into local code. Novel requests go to your model; proven behavior stays in your software.

### 6. Prompt optimization
- **How prompt optimization works:** Compresses system prompts or uses DSPy to refine instructions.
- **Why it is incomplete:** Reduces token counts slightly, but retains 100% of the network latency, API costs, and dependency on external model providers.
- **The Ink difference:** Ink replaces the remote call entirely for qualified states.

---

## Comparison summary

| Capability | Exact Cache | Semantic Cache | Static Classifier | Model Gateway | **Ink (Behavior JIT)** |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Execution Latency** | $< 0.1\text{ ms}$ | $5\text{--}25\text{ ms}$ | $1\text{--}10\text{ ms}$ | $200\text{--}800\text{ ms}$ | **$< 0.1\text{ ms}$** |
| **Inference Cost** | $\$0.00$ | Embedding cost | CPU cost | Token cost | **$\$0.00$ (Local Fast Path)** |
| **Verified Outcome Gate** | No | No | No | No | **Yes (Wilson 95% Bound)** |
| **Abstention on Novelty** | Cache miss only | Distance threshold | None (always predicts) | None | **Yes (Automatic Host Fallback)** |
| **Statistical Error Budget** | No | No | No | No | **Yes (Per-site contract)** |
| **Autonomous Deoptimization** | No | No | No | No | **Yes (Continuous verification)** |
| **Zero Cloud Dependencies** | Depends | Usually cloud vector DB | Local model | Cloud proxy | **Yes (Embedded SQLite)** |

*Note: Remote model and gateway latency ranges ($200\text{--}1,200\text{ ms}$) and semantic cache retrieval ranges reflect industry network and provider averages. Ink Fast Path latency (typically $< 0.01\text{ ms}$ for Exact, $< 0.10\text{ ms}$ for Linear) is directly measured in-process on local host CPU / Apple Silicon benchmarks.*

---

## Own what your AI has learned

When an AI system operates in production, its executions generate proprietary enterprise value. That value should not evaporate into ephemeral token logs.

Ink captures that behavioral data, qualifies it with statistical rigor, and compiles it into an owned software asset that you control.

> **Models handle novelty. Ink turns proven behavior into software.**
