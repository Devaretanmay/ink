# v0.4 implementation audit

This maps all 60 planned subtasks to implemented behavior and validation. “Verified”
below means local repository/wheel evidence; it does not imply cloud publication
or customer-production results. Detailed measurements: [validation](validation-v0.4.md).

## Task 1 — Decision primitive and compatibility

| # | Delivered | Evidence |
|---|---|---|
| 1 | Reuse/legacy inventory | `compatibility.md` |
| 2 | Private Python implementation and import shims | `internal/`, compatibility identity tests |
| 3 | Existing Rust exports preserved | Rust suite, legacy Python suite |
| 4 | Immutable typed DecisionSite and hashed revisions | Contract validation tests |
| 5 | Choice/source/ID/version/reason/recorded result | Decision API tests |
| 6 | Persistent client and explicit lifecycle | SQLite/restart tests |
| 7 | Client and default-client decision primitive | Public API and contract tests |
| 8 | Async fallback, cancellation, off-thread inference | Async and event-loop tests |
| 9 | Exactly-once fallback invocation; host owns actions | Exception/storage-failure tests |
| 10 | Verifier/version/evidence outcome reporting | Ledger, immutable outcome tests |
| 11 | Changed schema/choices/fallback revision requalify | Version tests |
| 12 | Contract and compatibility regression coverage | Full Python suite |

## Task 2 — History and profiler

| # | Delivered | Evidence |
|---|---|---|
| 1 | Separate local decision database | DecisionStore tests; episode store unchanged |
| 2 | Transactional schema v1→v2→v3→v4 migrations | Outcome preservation/rollback/repair plus queryable coverage/promotion/drift/lineage backfill tests |
| 3 | Site contracts persisted | Restart tests |
| 4 | Typed state, decision source, timing, task linkage | History/export tests |
| 5 | Measured model calls, tokens, optional cost/provider | Real local run, fixed-call tests |
| 6 | Delayed/idempotent outcomes; missing != failed | Outcome and profiler tests |
| 7 | Shadow choice/probability/confidence alongside fallback | Shadow pairing tests |
| 8 | Immutable artifacts, frozen profiles, lifecycle events | Integrity/recompile tests |
| 9 | Frequency, repetition, choices, completeness, usage | Profiler tests |
| 10 | Temporal splits; purge tasks crossing boundaries | Split/restart tests |
| 11 | WAL, transactions, concurrency, outage fallback | Threaded writers and rollback tests |
| 12 | Custom paths, export, backup, protected retention | Data-control tests |

The previous migration could delete factual outcomes. The fixed upgrade preserves
them; v3 repairs already-affected foreign keys and demotes active paths. Evidence
already deleted by an older run cannot be invented; restore a backup or recollect.

## Task 3 — Compilation and dispatch

| # | Delivered | Evidence |
|---|---|---|
| 1 | Pinned optional Laya runtime; checkpoint format and license notes | `laya-path.md` |
| 2 | Real configured checkpoint persisted/reloaded/inferred | Laya tests and real model run |
| 3 | Private compile/predict engine protocol | Exact and Laya implementations |
| 4 | Original fallback retained | Failure and novelty tests |
| 5 | Deterministic primitive/optional/numeric normalization | Contract tests |
| 6 | Compilation uses outcome-bearing training partition only | Stored dataset/partition provenance |
| 7 | Immutable descriptors, hashes, engine/checkpoint revision | Corruption/restart tests |
| 8 | Conservative exact typed-state coverage | Novel state and rare enterprise cases |
| 9 | Region confidence from held-out outcome bounds | Frozen calibration evidence |
| 10 | Dispatch requires active compatible supported path | Lifecycle and restart tests |
| 11 | Engine/artifact/storage failures return fallback | Failure tests |
| 12 | Cold/warm/size/RSS/dispatcher measurement | Checked-in benchmark summary |

Laya is configured, not fine-tuned. Exact-state coverage is intentionally narrower
than learned numeric neighborhoods. These are explicit v0.4 defaults, not hidden
substitutions for broader model generalization.

## Task 4 — Qualification and continued evaluation

| # | Delivered | Evidence |
|---|---|---|
| 1 | Persisted lifecycle and demotion epochs | Lifecycle tests |
| 2 | Explicit maintenance waits for enough independent support | Readiness/early-candidate replacement tests |
| 3 | Candidate shadows; fallback controls execution | Shadow tests |
| 4 | Pair factual outcome only with executed choice | History and ledger evidence |
| 5 | Agreement never transfers an outcome | Independent-verifier rejection test |
| 6 | Isolated replay verifier contract | Refund ledgers; missing verifier remains shadow |
| 7 | Per-region and aggregate uncertainty/quality/agreement | Promotion report |
| 8 | Explicit configurable sample/quality/degradation gates | Requirement and promotion tests |
| 9 | Measured calibration freezes supported regions and confidence | Profile hash and untouched evaluation partition |
| 10 | Evidence and active switch committed together | Transaction tests and lifecycle event order |
| 11 | Randomized fallback comparison plus outcome evaluation | Real active phase, comparison usage |
| 12 | Demotion and fresh shadow requalification | Drift, missing-outcome, verifier-change tests |

Application quality tolerances remain explicit experiment settings; Microloop does
not infer a customer's acceptable degradation. The demo's settings are permissive
mechanics-proof settings. There is no universal production promotion default.

## Task 5 — Operational proof and delivery

| # | Delivered | Evidence |
|---|---|---|
| 1 | Refund operational agent | `examples/refund_agent` |
| 2 | Real local fallback and remote-provider adapter | 6,419 actual local model calls; cloud adapter live run unverified |
| 3 | Action ledger and independent replay | Persisted ledger and verifier tests |
| 4 | Thousands of cases, rare states, novelty, drift | 7,209-case report |
| 5 | Generated/fixture/local-model provenance explicit | Reports and documentation |
| 6 | Observe→compile→shadow→promote→serve→fallback→demote | All seven real-run acceptance checks passed |
| 7 | Sites CLI and JSON summaries | Installed CLI tests |
| 8 | Site inspection and legacy file compatibility | Existing/new CLI tests |
| 9 | Compile/evaluate/maintenance, no hidden worker | CLI and maintenance tests |
| 10 | Actual usage and verified fixed-call savings separated | 405 verified vs 790 total bypassed calls |
| 11 | New docs, metadata, migration notes, legacy documentation | README, docs, 0.4.0 metadata |
| 12 | Checks, fresh wheel, offline demo, real engine/model evidence | Validation report and machine-readable summaries |

## Remaining external gates

Cloud/frontier-provider validation needs configured credentials. Customer-production
coverage/outcome claims need actual customer traffic. Publishing a distribution is
a separate release action. No hosted product, auto-mining, dashboard, or provider
router was added.
