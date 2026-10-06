# Threat Model

A realistic security analysis of the Ink Decision JIT SDK, covering potential threats, real-world mitigations, and known residual risks.

---

## Architecture Boundaries

```text
[Host Application / Agent]
         │ (Python function calls)
         ▼
[Ink Runtime (in-process)]
   ├── ExactEngine (memory)
   ├── DecisionModelEngine (MLX local process)
   └── DecisionStore (local SQLite file)
```

Ink runs entirely **in-process** within the host application. There are no daemon processes, background network listeners, or cloud control planes.

---

## Threat Matrix

| Threat | Likelihood | Impact | Existing Mitigation | Residual Risk |
| :--- | :--- | :--- | :--- | :--- |
| **Local state exposure** | Low–Med | Medium | SQLite file is created with default OS file permissions. Encrypted volume recommended for sensitive environments. | If the host filesystem is compromised, `.ink/decisions.db` is readable. |
| **Malicious / corrupt model artifact** | Low | High | SHA-256 checksums are verified on artifact load. Checksum mismatch causes immediate rejection. | An attacker with write access to `.ink/` could alter both artifact and stored checksum unless write-protected. |
| **Unsafe deserialization** | Low | High | Ink uses strict `json.loads` exclusively. **No `pickle` or arbitrary code execution deserializers are used.** | Standard JSON parser DoS vectors (deeply nested objects) mitigated by schema checking. |
| **Poisoned outcomes (fake quality signals)** | Medium | High | Outcomes require explicit verifier identity and version. Statistical Hoeffding bound requires multiple independent samples. | If host verification logic is compromised, corrupted outcomes can promote faulty fast paths until drift detection catches it. |
| **Fake verifier signals** | Low | High | Evidence identity tracking links decisions to specific verifier implementations. Changing verifier identities invalidates prior evidence. | A verifier that consistently reports 1.0 quality regardless of true success will improperly qualify paths. |
| **Incorrect qualification** | Medium | Medium | Shadow period requires held-out validation; statistical confidence bounds must exceed site requirements. | Statistical bounds are probabilistic, not absolute proofs. |
| **Drift detection lag** | High | Low–Med | Comparison traffic (5–35%) continuously samples live model responses to detect policy drift. | In benchmark testing, **7–8 incorrect decisions were served** before passive drift was detected and demotion occurred. |
| **Database corruption / SQLite locking** | Low | Low | All writes use SQLite transactions and WAL mode. On any storage error, Ink **fails open** — executing the host model fallback transparently. | Concurrent multi-process writes may cause contention; fail-open ensures no request is dropped. |
| **Dependency compromise** | Low | High | Minimal runtime dependency set: `numpy`, `mlx`, `tokenizers`, `huggingface-hub`. No web frameworks or network servers. | Standard supply-chain risk for Python packages; pin versions in production lockfiles. |

---

## Fail-Open Security Guarantee

Ink's primary reliability invariant is **strict fail-open operation**:

```text
Any Exception in Ink (SQLite error, MLX failure, memory limit, timeout)
                     │
                     ▼
             Execute Host Fallback
                     │
                     ▼
       Agent behaves exactly as if Ink were not present
```

Ink will never crash your agent or drop a user request due to an internal optimization failure. If anything goes wrong, the request routes to your original model.

---

## Drift and Serving Errors

We are explicit about this limitation:

> **Ink does not provide zero-error serving.** Under policy drift (where downstream business rules change without updating the DecisionSite contract), Ink served **7–8 incorrect decisions** in benchmark testing before the quality drop crossed the demotion threshold.

For comparison, a static cache under the same conditions produced an ongoing **12.0–18.0% error rate indefinitely** because it had no active comparison traffic or automated revocation.
