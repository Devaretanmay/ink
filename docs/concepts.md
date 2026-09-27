# Decision JIT concepts

- **Decision site:** a named bounded choice with a typed state contract. Contract
  hashes include field types, choices, and the declared fallback revision.
- **Fast path:** an immutable local engine artifact plus a frozen qualification
  profile. The artifact proposes a choice; the host executes it.
- **Coverage:** the exact typed states supported by calibration evidence in v0.4.
  Numeric values normalize consistently, but nearby amounts are not interchangeable.
- **Shadow run:** the candidate predicts while the original fallback retains control.
- **Outcome:** independent evidence about an executed or replayed action, linked to
  its decision and verifier version. Missing evidence is never treated as success.
- **Promotion:** an evidence-backed change from shadow to active service.
- **Fallback:** the original agent/model callable. It handles novelty, uncertainty,
  comparison traffic, and engine failures.
- **Profiler:** per-site observations, state/choice frequency, outcome completeness,
  measured usage, source counts, and lifecycle evidence.

Measured model usage comes only from `FallbackResult`. A string-returning fallback
has unknown model usage. `fallbacks_avoided` counts bypassed callable invocations;
`verified_fast_path_decisions` counts successful outcome-bearing fast-path choices.
`model_calls_avoided` remains unknown because skipped callables may have variable
inference behavior. Token/cost savings are not fabricated from fixture runs.

The old runtime vocabulary is documented in [legacy concepts](legacy/concepts.md).
