# Limitations & Non-Goals

Ink is designed for **bounded, repeated, verifiable semantic decisions**. Understanding where Ink does *not* apply is essential for successful deployments.

---

## What Ink is NOT

### 1. NOT an Open-Ended Text Generator
Ink cannot write essays, compose poetry, generate source code, or conduct open-ended conversational dialogue. If the decision cannot be expressed as choosing among discrete options (`choices`), Ink is the wrong tool.

### 2. NOT a Semantic Cache
Semantic caches store prior responses and return them whenever an incoming query has high cosine similarity to an old prompt. Ink rejects this model because:
- High vector similarity does not imply identical operational validity.
- Semantic caches silently serve outdated answers during policy drift.
- Ink requires verified real-world outcomes before granting serving authority.

### 3. NOT an Offline Model Training Pipeline
Ink is not a replacement for offline model fine-tuning frameworks (Unsloth, Axolotl, vLLM). Ink is a runtime JIT that operates inside your production application, observing live behavior and compiling local software branches.

---

## Workload Disqualification Checklist

Do NOT use Ink if:

- **State Entropy is 100% Unique:** If every single request contains high-entropy randomness (e.g. random nonces, unique cryptographic hashes, completely novel conversational queries with zero repeated intent), a Fast Path will never qualify.
- **Outcomes Cannot Be Measured:** If your system has no way of verifying whether a decision succeeded or failed, Ink will remain in `OBSERVE` mode indefinitely.
- **Decision Space Exceeds 100+ Choices:** Ink is optimized for bounded decision spaces (typically 2 to 30 choices). Continuous high-cardinality routing is better handled by hierarchical decision trees or specialized retrieval models.
