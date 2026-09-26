# Changelog

All notable changes to Microloop are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [0.3.0] — 2026-09-26

The productization release. Microloop is now a developer-ready local reliability
runtime rather than a benchmark harness.

### Added

- Frozen public API: `Event`, `Monitor`, `Decision`, `Policy`,
  `ProgressState`, `InterventionAction`.
- Canonical structured event model with `state`, `metrics` and `metadata`.
- Decoupled detection and policy: `Monitor.observe` classifies, `Policy`
  decides the intervention.
- Rust workspace with `crates/microloop-core` and `python/microloop`.
- CLI: `microloop inspect`, `microloop replay`, `microloop monitor` (live view,
  `--follow`) and `microloop doctor`.
- `microloop.wrap(agent)` agent wrapper returning a `RunReport`.
- Schema `0.3.0` trajectory JSONL and a deterministic `replay`.
- Dual MIT / Apache-2.0 licensing.

### Changed

- Detection results are exposed as progress states; individual detectors are
  internal and surfaced only in `decision.reasons`.
- Automatic `replan`/`stop` is opt-in and rate-limited by cooldown and cap.

### Removed

- Legacy HTTP-proxy gate code, C FFI surface and `cbindgen` header generation.
- Private product documents, benchmark specifications and synthetic evidence
  generators from the public tree.
- Committed raw benchmark run directories.

## [0.2.0] — 2026-07-01

- Experimental trajectory monitoring and policy engine.
