# Decision runtime architecture

The Python `Microloop` client owns site registration, dispatch, compilation,
verification, and maintenance. Rust's existing trajectory and controller exports
remain compatibility infrastructure; the decision JIT does not depend on them
for qualification. Package version (0.4.0) is independent of the trajectory
schema (0.3.0) and the decision-store schema (version 4).

## Data flow

1. Validate and normalize a typed state against the versioned site contract.
2. Load the current artifact and frozen calibration profile; verify their hashes.
3. Abstain outside exact-state coverage, without adequate calibrated support, or
   when the engine is unavailable. Randomly retain a fraction of covered decisions
   for original-model comparison.
4. Persist the decision before returning an active fast-path choice. If persistence
   fails, invoke the original fallback and return `recorded=False` if logging also
   fails. Applications must not report an outcome against an unrecorded ID.
5. The host executes its action and reports an immutable, idempotent outcome.
6. Explicit maintenance compiles, verifies/promotes, or re-evaluates active paths.

## Components

`decision_api.py` is the orchestration surface. Private modules in
`microloop.internal` hold contracts, the SQLite store, engine adapters, and
verification calculations. Legacy trajectory/controller code lives alongside
these modules behind unchanged compatibility imports.

SQLite schema version 4 contains sites, decisions, factual outcomes, immutable
artifacts, profiles, promotion evidence, and append-only lifecycle events, plus
queryable per-state coverage, promotion records, drift checks, and artifact
lineage. WAL,
foreign keys, bounded lock waits, and transactions protect concurrent connections.
The previous episode database is separate. Read-only CLI queries open the decision
database in read-only mode. Export takes a consistent snapshot; retention protects
all evidence for sites with artifacts.

## Evidence boundary

Training, calibration, and evaluation are chronological; tasks crossing split
boundaries are purged. Compilation uses only factual fallback choices with
outcomes. Laya uses a pretrained local checkpoint configured with up to three
training examples; it does not fine-tune weights. The exact engine fits a frequency
table. Both use identical typed-state coverage and qualification logic.

An independent replay verifier executes/checks each proposed action and the
fallback choice separately. Raw Laya scores stay uncalibrated (the MLX runtime
clamps out-of-range checkpoint temperatures); only held-out quality bounds count
as confidence. Outcome evidence is never copied from one action to
another. Bounded task-level scores use one-sided 95% Hoeffding bounds; independence
between task groups is an assumption, not a property Microloop can prove.
Calibration gates coverage by region. Held-out and fresh shadow evaluations must
pass overall and for every covered region, using the frozen verifier identity.

Outcome-preserving migrations repair the earlier broken child foreign key;
affected active paths return to shadow when factual evidence was lost.

An atomic promotion records SHADOW → VERIFIED → ACTIVE. Active paths retain random
fallback comparisons. Maintenance or re-evaluation ticks demote on outcome
degradation, missing evidence in a full window, insufficient comparison evidence,
or excessive uncovered traffic; demotion never runs inside decision requests, so
serving never slows for qualification work. Demotion
resets the shadow evidence epoch. Recompilation retires the previous
artifact and starts qualification from scratch.

Laya runtime version and checkpoint hashes are bound to the artifact. The engine
checks them on load, keeps loaded weights local, and abstains on inference errors.
No model artifacts, state, or evidence are uploaded by the runtime.
