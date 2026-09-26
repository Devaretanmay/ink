# Changelog

All notable changes to Microloop are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [0.3.0] — unreleased

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
- Schema `0.3.0` trajectory JSONL with real compatibility checking: a
  trajectory whose `schema_version` major version differs from the runtime's is
  rejected instead of being analyzed on a guess.
- Dual MIT / Apache-2.0 licensing, with both license texts shipped in the wheel.
- Benchmark provenance gate: every run bundle declares `run_mode`
  (`real`/`simulated`), simulated runs are confined to their own directory, and
  the report generator rejects anything that is not `real`.
- `pip install microloop` published from the release workflow, continuing the
  existing `microloop` distribution (0.1.1 .. 0.2.0).

### Changed

- Detection results are exposed as progress states; individual detectors are
  internal and surfaced only in `decision.reasons`.
- Automatic `replan`/`stop` is opt-in and rate-limited by cooldown and cap.
- The Rust detection internals (engine, detectors, history window,
  canonicalization) are private. Only `Monitor`, `Event`, `Decision`, `Policy`,
  the configs, the progress state and the intervention are public.
- `MonitorConfig` moved out of the event module into its own `config` module.
- `microloop inspect` now reports detection under the default observation-only
  policy, so it no longer displays a `replan` that a normal runtime would not
  produce. `replay` and `monitor` still show recommended interventions, labelled
  as recommendations.
- `severity` is documented as a fixed status-to-number lookup, explicitly not a
  probability or confidence.
- Trajectory schema compatibility is checked at the CLI boundary; this is
  documented rather than enforced inside the library, which stays
  version-agnostic.
- The bounded history window is a real `VecDeque` ring buffer, so eviction is
  O(1) at the front instead of an O(n) `Vec::remove(0)` shift.
- README states plainly that no benchmark result is currently published as
  verified evidence, rather than quoting a figure the repository cannot
  reproduce.

### Removed

- Legacy HTTP-proxy gate code, C FFI surface and `cbindgen` header generation.
- Private product documents, benchmark specifications and synthetic evidence
  generators from the public tree.
- Committed raw benchmark run directories and committed benchmark-derived
  result JSON.
- The experimental `microloop-compress` crate, which was never part of the
  public product surface.
- A repository-wide macOS linker override in `.cargo/config.toml`.
- `microloop.wrap` / `MonitoredAgent` / `RunReport`. The agent wrapper's
  duck-typed contract is not committed to as a stable API in this release; the
  host-owned loop over `Monitor` is the supported integration. See
  `docs/integration.md`.

## [0.2.0] — 2026-07-01

- Experimental trajectory monitoring and policy engine.
