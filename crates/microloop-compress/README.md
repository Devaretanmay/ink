# Microloop Compact Context Representation (CCR)

This crate implements Compact Context Representation (CCR) and compression primitives.

### Status in Microloop v0.3 / Experiment 001
* **Role:** Excluded from the default v0.3 validation benchmark workspace to eliminate experimental confounders and isolate trajectory recovery dynamics.
* **Planned Role in v0.4+:** Will serve as the underlying engine for **Checkpoint Compaction**, preserving ground-truth execution history in durable storage while feeding compressed representations to long-horizon agent contexts.
* See [Repository Scope](../../docs/REPOSITORY-SCOPE.md) and [System Architecture](../../docs/ARCHITECTURE.md).
