# microloop-compress

Compact Context Representation (CCR) and content-compression primitives.

**Status:** internal and experimental. This crate is deliberately excluded from
the Microloop v0.3 Cargo workspace so the released runtime stays focused on
trajectory progress detection and recovery policy. It is not part of the public
product surface or the Python SDK.

Its intended future role is trajectory compaction: summarizing long, stalled
histories while preserving execution ground truth for replay.

See the root [README](../../README.md) and
[docs/architecture.md](../../docs/architecture.md) for the shipped runtime.
