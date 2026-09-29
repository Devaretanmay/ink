# v0.3 compatibility: removed

The pre-0.4 trajectory surface (`Monitor`, `Event`, `Decision`, `Policy`,
`ProgressState`, `InterventionAction`, the `Runtime*` controllers/adapters,
`EpisodeStore`, `microloop.compat`, `microloop.store`, `microloop.runtime`,
and the legacy CLI views `replay`, `monitor`, `explain`, `stats`, `doctor`)
was removed. The decision API (`Microloop`, `DecisionSite`, `decision`,
`record_outcome`) is the only runtime surface.

Details:

- The Rust trajectory engine (`microloop-core`) was removed with its Python
  bindings. The native extension now exposes only `microloop_core.version()`.
- Trajectory schema `0.3.0` is no longer read. Existing
  `.microloop/episodes.db` files are left untouched on disk but never opened.
- Decision history keeps its own versioned SQLite schema (currently version 4,
  with transactional 1→2→3→4 migrations) under `.microloop/decisions.db`.
- `docs/legacy/` preserves the v0.3 documentation as history.
