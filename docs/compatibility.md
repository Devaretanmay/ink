# v0.3 compatibility and infrastructure inventory

| Existing component | v0.4 treatment |
|---|---|
| Rust trajectory engine, detectors, controller | Preserved, unchanged public crate exports |
| Python Monitor, Event, Policy, progress types | Implementation in `internal.legacy`; root imports retained |
| Runtime state, usage, episodes, outcomes, adapters | Implementation in `internal.runtime`; old module paths retained |
| EpisodeStore, controller traces, adaptation history | Implementation in `internal.episode_store`; old store import retained |
| Existing verifiers | Retained for legacy runs; not automatically accepted as decision outcome verifiers |
| Old CLI views and coding examples | Compatibility tools; no longer primary product examples |
| Decision contracts, profiler, engine, verifier, store | New Python-owned decision subsystem |

Trajectory schema stays `0.3.0`; package version is independent. Decision history
uses a separate versioned SQLite schema (currently version 2, with transactional
1 to 2 migration). Existing `.microloop/episodes.db` is never
renamed or rewritten by the new decision client. Site summaries come from
`internal.profiler`; calibration profiles stay frozen on artifacts.

Old imports remain aliases to the moved classes. Avoid depending on class
`__module__` strings or private helpers. The implementation preserves existing test
coverage rather than removing old functionality to simplify the product story.
