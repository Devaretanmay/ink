# Why Ink

Production AI systems accumulate behavioral knowledge over time. Once that behavior is independently proven, it should become software instead of being rented repeatedly from a general-purpose model.

Ink is the Behavior JIT that turns proven model behavior into owned software.

---

## The Core Problem

Every day, production AI agents and applications make millions of bounded decisions:
- Selecting which tool to execute from a fixed registry
- Routing inbound customer requests to specialist teams
- Triaging security alerts and incident severity
- Evaluating workflow approval and risk gates

Today, teams pay frontier models repeatedly for these decisions. Every request costs network latency ($200\text{--}1200\text{ ms}$), token fees, and operational exposure to third-party model changes.

Even after a system has made the same decision ten thousand times with verified success, it calls the model again.

**Your AI should not pay to rethink what it already knows.**

---

## Prediction vs. Permission

The central thesis of Ink is:

> **Prediction and permission are different things.**

A classifier predicts what choice might be correct. A fine-tuned model predicts. A prompt-optimized model predicts.

None of them decide whether local execution has earned the right to serve live traffic. None of them quantify statistical risk against an explicit error budget. None of them automatically revoke their own serving authority when business reality shifts.

Ink is not a prediction engine. **Ink is an authority lifecycle.**

---

## Ink Compared to Alternatives

### 1. Caching (Exact & Semantic)
- **How caching works**: Hashes inputs or performs approximate nearest-neighbor search on embeddings. Returns previous outputs on hit.
- **Why it breaks in production**: Caches do not evaluate outcome correctness. If a model made a mistake yesterday, a cache faithfully repeats that mistake today. Caches cannot generalize safely across structured state variations, cannot enforce statistical error budgets, and have no concept of verified outcomes.
- **Ink difference**: Ink requires independent outcome verification before candidate behavior can qualify. Ink serves through compiled execution boundaries, not raw memory recall.

### 2. Static Rules & Heuristics
- **How rules work**: Engineers manually write `if/else` statements or regex patterns.
- **Why they break in production**: Natural language and agent state spaces are too high-dimensional for manual rule maintenance. Rules become brittle, conflict with one another, and fail silently as user behavior evolves.
- **Ink difference**: Ink compiles execution boundaries automatically from observed behavior and verifies them continuously against production outcomes.

### 3. "Just Train a Classifier"
- **How classifiers work**: An engineer trains a classical model (logistic regression, gradient boosting, or small BERT) on logged outputs.
- **Why they break in production**:
  - Classifiers predict on every input, even when an input is totally out-of-distribution.
  - Classifiers do not know when to abstain.
  - Classifiers cannot detect distribution shift autonomously.
  - Classifiers provide no fail-open fallback back to the frontier model.
- **Ink difference**: Ink grants local serving authority only to inputs within qualified coverage regions. Unseen, novel, or ambiguous requests automatically fall back to the Host model.

### 4. Fine-Tuning Smaller Models
- **How fine-tuning works**: Distills frontier model outputs into a 1B–8B parameter open-weights model hosted on dedicated GPU infrastructure.
- **Why it is incomplete**:
  - Still requires GPU inference infrastructure, cold starts, and hosting costs.
  - Still produces non-deterministic generation with tail latency ($50\text{--}300\text{ ms}$).
  - Lacks runtime safety contracts: a 3B model cannot tell you when its own distribution has drifted.
- **Ink difference**: Qualified Fast Paths execute in sub-milliseconds on CPU or Apple Silicon without GPU infrastructure. When behavior is proven enough, the cheapest model is no model.

### 5. Model Routing
- **How routing works**: A gateway selects whether to call GPT-4o, Claude 3.5 Sonnet, or GPT-4o-mini based on query complexity.
- **Why it is incomplete**: Model routing still pays model providers for every single request. It optimizes rental rates rather than building owned software assets.
- **Ink difference**: Ink turns proven decisions into local code. Novel requests go to your Host model; proven behavior stays in your software.

### 6. Prompt Optimization
- **How prompt optimization works**: Compresses system prompts or uses DSPy to refine instructions.
- **Why it is incomplete**: Reduces token count slightly, but retains 100% of the network latency, API costs, and dependency on external model providers.
- **Ink difference**: Ink replaces the remote call entirely for qualified states.

---

## Comparison Summary

| Capability | Exact Cache | Semantic Cache | Static Classifier | Model Gateway | **Ink (Behavior JIT)** |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Execution Latency** | $< 0.1\text{ ms}$ | $5\text{--}25\text{ ms}$ | $1\text{--}10\text{ ms}$ | $200\text{--}800\text{ ms}$ | **$< 0.1\text{ ms}$** |
| **Inference Cost** | $\$0.00$ | Embedding cost | CPU cost | Token cost | **$\$0.00$ (Local Fast Path)** |
| **Verified Outcome Gate** | No | No | No | No | **Yes (Wilson 95% Bound)** |
| **Abstention on Novelty** | Cache miss only | Distance threshold | None (always predicts) | None | **Yes (Automatic Host Fallback)** |
| **Statistical Error Budget** | No | No | No | No | **Yes (Per-site contract)** |
| **Autonomous Deoptimization** | No | No | No | No | **Yes (Continuous verification)** |
| **Zero Cloud Dependencies** | Depends | Usually cloud vector DB | Local model | Cloud proxy | **Yes (Embedded SQLite)** |
 
*\*Note: Remote model and gateway latency ranges ($200\text{--}1200\text{ ms}$) and semantic cache retrieval ranges reflect industry network and provider averages. Ink Fast Path latency (typically $< 0.01\text{ ms}$ for Exact, $< 0.10\text{ ms}$ for Linear) is directly measured in-process on local host CPU / Apple Silicon benchmarks.*

---

## Own What Your AI Has Learned

When an AI system operates in production, its executions generate proprietary enterprise value. That value should not vanish into ephemeral token logs.

Ink captures that behavioral data, qualifies it with statistical rigor, and compiles it into an owned software asset.

> **Models handle novelty. Ink turns proven behavior into software.**
