# Alternatives Comparison

How Ink compares to related optimization and reliability techniques across the AI engineering landscape.

---

## Comparison Matrix

| Technique | What it does | When it works best | Main risk | Where Ink differs |
| :--- | :--- | :--- | :--- | :--- |
| **Exact Cache** (e.g. Redis hash) | Returns identical response for exact byte-match input key | Strictly deterministic, immutable queries (e.g. static lookups) | Stale responses when underlying reality changes; zero drift detection | Ink requires independent outcome evidence before serving, and uses ongoing comparison traffic to revoke stale decisions. |
| **Semantic Cache** (e.g. GPTCache) | Returns cached response if embedding similarity exceeds threshold | High query similarity with high tolerance for approximate answers | False matches ("similar" does not mean "same answer"); no outcome verification | Ink does not use embedding similarity to serve unverified decisions. Serving authority requires shadow qualification with real verifier signals. |
| **Model Routing** (e.g. RouteLLM) | Classifies query complexity to pick cheaper/smaller remote model | Broad mix of simple vs. complex queries | Every request still incurs remote network latency and API cost | Ink aims to eliminate the remote model call entirely for qualified bounded decisions (<0.2 ms local execution). |
| **Small-Model Distillation** | Distills large model behavior into a dedicated SLM (e.g. 1B–3B) | Large, homogeneous, stable workloads with available training data | High initial training cost; static snapshot degrades silently under distribution shift | Ink operates continuously in production: auto-profiles candidate sites, qualifies via shadow execution, and deoptimizes on drift. |
| **Fine-Tuned Classifier** | Trains a small specialized model on historical labeled data | When the classification boundary and taxonomy are known and stable | Requires manual data labeling, retraining pipelines, and drift monitoring | Excellent for stable tasks. Ink is designed for emergent decisions where behavior evolves and requires automatic qualification and deopt. |
| **Hard-Coded Workflow** (Rules/Regex) | Deterministic if/else business logic written by engineers | When decision rules are 100% understood and rarely change | High engineering maintenance; brittle to edge cases and linguistic variety | Ink observes actual model behavior to build candidate paths automatically rather than requiring manual rule authoring. |
| **Ink (Behavior JIT)** | Discovers, shadows, qualifies, and serves verified bounded decisions locally | Repetitive bounded decisions with measurable downstream outcome feedback | Under passive drift, may serve 7–8 wrong decisions before revoking | Combines local execution speed (<0.2 ms) with continuous statistical qualification and automatic fallback. |

---

## Detailed Category Distinctions

### 1. Ink vs. Semantic Caching

Semantic caching answers the question: *"Has someone asked something similar before?"*
Ink answers the question: *"Has this specific decision pattern been proven correct by real downstream outcomes, and is it still performing accurately today?"*

Semantic caching assumes that semantic proximity implies answer validity. In agentic workflows, subtle prompt differences (e.g. "refund customer #123" vs. "charge customer #123") have nearly identical embeddings but require opposite actions. Ink relies on **independent verifier evidence**, not vector similarity.

### 2. Ink vs. Fine-Tuned Classifiers

If you already have 10,000 clean, human-labeled examples of ticket categorization and the categories never change, **a fine-tuned classifier is the right choice**. You should build and deploy one.

Ink is valuable when:
1. You do not want to manage a separate ML training, deployment, and monitoring pipeline for every small decision point.
2. The agent's decision logic emerges dynamically from system prompts and tool feedback.
3. You need the system to automatically fall back to the large model when novel scenarios arise.
4. You need automated deoptimization when downstream business conditions drift.

---

## Summary

Use **hard-coded rules** when the logic is static and known.
Use **fine-tuned classifiers** when you have stable labeled datasets and ML ops infrastructure.
Use **model routing** when every request genuinely requires novel reasoning from an LLM.
Use **Ink** when production agents make repetitive, bounded decisions that can be independently verified against real outcomes.
