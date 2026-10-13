# Changelog

All notable, user-facing changes to Ink. This project follows
[Semantic Versioning](https://semver.org/).

The PyPI distribution is **`ink-jit`** (`pip install ink-jit`); the Python import is
**`ink`** and the CLI is **`ink`**.

## [0.6.1] - 2026-10-10

- Completely removed experimental web console and dashboard server.
- Streamlined CLI command surface to local inspection, discovery, and compilation.
- Refined and audited all documentation and metadata.

## [0.6.0] - 2026-10-10

- Repositioned the project around one idea: *models handle novelty; Ink turns proven
  behavior into software.* README, docs, examples, package metadata, and benchmark framing
  now describe a single product with a single worldview.
- `ink discover` is the primary evaluation entry point.
- Statistical safety model with Wilson score interval confidence bounds.
- Separation of representation compilation from serving authority.
- Curated documentation and runnable examples for LangGraph and PydanticAI.

## [0.5.0] - 2026-10-02

- Zero-touch DecisionSite discovery and trace ingestion (OpenTelemetry, LangSmith, LiteLLM).
- Economic DecisionSite profiling (`ink discover --profile`) with repetition, drift
  sensitivity, and net-value estimates.
- Multi-site fleet management and large-scale local verification, still zero cloud dependencies.
- Explicit public benchmark claim governance, including false-serve accounting.

## [0.4.0] - 2026-09-28

- Ink Decision v1: bundled local decision model, mandatory runtime dependencies, neural
  compilation by default, and checksum-verified model provisioning. Linux and macOS
  runtime targets; Windows removed from wheel CI.
- Typed decision SDK with synchronous and asynchronous fallbacks, durable outcome
  reporting, exact-state coverage, and local engine dispatch.
- Shadow qualification with frozen calibration profiles, independent replay verification,
  atomic promotion, comparison sampling, and drift demotion.
- Decision SQLite schema v3 with transactional upgrades, audit export, backups, and
  read-only inspection.
- Decision CLI, a generated refund workload, real remote/local model fallback modes, and a
  reproducible local inference benchmark.
- Legacy trajectory-monitoring implementation moved behind `ink.internal`.

### Fixed

- A v1→v2 migration could cascade-delete outcomes and leave a broken foreign key. Upgrades
  now preserve evidence, and v3 repairs affected schemas. Missing shadow outcomes, changed
  verifier identities, storage failures, and invalid engine results can no longer silently
  establish or retain qualification.

## [0.3.0]

The productization release: Ink became a developer-ready local runtime rather than a
benchmark harness.

- Frozen public API (`Event`, `Monitor`, `Decision`, `Policy`, `ProgressState`,
  `InterventionAction`) and a canonical structured event model.
- CLI: `ink inspect`, `ink replay`, `ink monitor`, `ink doctor`.
- Trajectory schema `0.3.0` with real compatibility checking at the CLI boundary.
- Apache-2.0 licensing, with the license text shipped in the wheel.
- Benchmark provenance gate: simulated runs are confined to their own directory and
  reports reject anything that is not `real`.

### Removed

- Legacy HTTP-proxy gate code, the C FFI surface, the experimental `ink-compress` crate,
  and private product documents and synthetic evidence generators from the public tree.
- `ink.wrap` / `MonitoredAgent` / `RunReport`; the host-owned loop over `Monitor` is the
  supported integration.

## [0.2.0] - 2026-07-01

- Experimental trajectory monitoring and policy engine.

## [0.1.x]

- Initial `ink` distribution: early trajectory analysis prototypes.
