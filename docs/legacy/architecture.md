# How the engine computes progress

```
microloop-core (Rust)
├── event.rs          canonical Event, ProgressState, Reason, Evidence
├── canonical.rs      volatile-token masking + error signatures
├── history.rs        bounded window of derived Record signals
├── detectors/        pure functions over the window
│   ├── repetition.rs   recurrence, exact and normalized
│   ├── error.rs        repeated error signatures
│   ├── stagnation.rs   verification results that stopped moving
│   ├── oscillation.rs  A/B environment state cycles
│   └── verification.rs verifier progress/regression comparison
├── engine.rs         ProgressEngine: features -> progress state
├── policy.rs         Policy: progress state -> intervention (compatibility)
├── runtime/          adaptive runtime layer
│   ├── state.rs        RuntimeState, Usage, Budget
│   ├── action.rs       RuntimeAction vocabulary (3 enabled, 6 experimental)
│   ├── capabilities.rs Capabilities and CapabilityLevel
│   ├── decision.rs     ProgressSnapshot, RuntimeDecision, RecommendationReason,
│   │                   ActionScore, ControllerTrace, Strategy
│   ├── rules.rs        candidate actions + reasons (what is possible)
│   ├── outcome.rs      ActionOutcome, ActionAttempt, StallEpisode
│   ├── scoring.rs      ActionScorer, HeuristicScorer, ScoringConfig
│   └── controller.rs   RuntimeController: gating + rule/scored selection
├── monitor.rs        Monitor: facade returning Decision
└── config.rs         MonitorConfig: detection bounds and their validation
```

The Python package `python/microloop` wraps the core through a thin PyO3 layer
(`python/microloop/src/lib.rs`) that marshals events and decisions as JSON. No
detection or policy logic lives in the bindings.

All provider code lives outside the package, in the repository's
`integrations/` tree (`integrations/coding_harness`, `integrations/openai_agents`)
and its `experiments` runner. Adapters own the model ladder, the compaction
implementation and the actuation; the core still only ever recommends a
direction. This is what keeps the engine from growing provider-specific
branches.

## Pipeline

```text
events (+ runtime)
  -> trajectory features
  -> progress state
  -> progress snapshot
  -> runtime state
  -> runtime recommendation
```

Detection and runtime adaptation are independent. Progress answers *is this run
advancing?*; the runtime layer answers *under what conditions, and what should
change?* Detection never knows about runtime actions.

## Data flow

1. `Monitor.observe(event)` validates the step and forwards it to
   `ProgressEngine`.
2. The engine extracts a `Record` (failure flag, error signature, verifier
   identity, failure count, normalized strings, state key) and handles verifier
   semantics: scope switches reset history, cached verifications are ignored,
   and a lower failure count is verified progress.
3. Detection runs over the bounded window. Verified progress suppresses the
   other detectors for that step.
4. `ProgressState` is synthesized: `regressing` dominates, then `stalled`, then
   `warning`, defaulting to `healthy`.
5. `ProgressSnapshot` summarizes the state, its detector `signals`, the step and
   `since_step` (the step at which the current state began).
6. `RuntimeController::decide` takes the snapshot, the reported `RuntimeState`,
   adapter `Capabilities` and any per-step `available` set. An exhausted
   `Budget` stops the run. Otherwise `rules::candidates` lists the possible
   actions for the state (context pressure, a stall, recovery) and gating keeps
   those that are capable, available, off cooldown and within the per-run cap.
   Under the `Rule` strategy the controller takes the first surviving rung;
   under `Scored` every survivor is scored by the `ActionScorer` (expected
   progress, cost, repetition, runtime pressure) and the highest score wins
   unless it does not clear the stability margin, in which case `continue` is
   chosen. Nothing permitted degrades to `continue`.
   `RuntimeController` owns this state, not `Policy`; `Policy` only supplies a
   converted configuration.
7. `Monitor` fills the nested `progress`, `runtime` and `recommendation` fields,
   keeps `status`/`intervention` in sync, and attaches the feedback text. A
   scored decision also attaches its `ControllerTrace`.
8. The `Decision` is returned. The runtime performs no I/O and executes nothing.
9. Between decisions the controller judges pending attempts against their
   evaluation horizon, records the `ActionOutcome`, and opens or closes the
   `StallEpisode`. This is pure state, not I/O: the engine still replays
   identically. Actuation itself stays host-side.

## Normalization

`canonical.rs` masks ANSI escapes, UUIDs, git/docker hashes, hex addresses,
timestamps, temp paths, PIDs and ephemeral ports, then collapses whitespace and
truncates to 256 bytes. Normalized comparison lets `normalized_repetition`
detect structurally identical steps whose volatile tokens differ. Error
signatures are the first non-empty line of an observation, normalized.

## Cost

The engine is a fixed amount of work per step: five pure functions over a window
of at most 32 records, plus the masking pass in `canonical.rs`. Nothing scales
with run length.

Measured with `make perf` (Python 3.13, Apple M4, release build, window full):

| Traffic | median | p99 |
|---|---|---|
| healthy, distinct commands | 48 µs | 56 µs |
| a verifier reporting improvement | 92 µs | 103 µs |
| the same test failing repeatedly | 112 µs | 122 µs |

Traced memory is 1 KiB after 100,000 steps, because the window is bounded. The
numbers are machine-specific; the ratios are the point.

## Determinism

The engine is a pure function of the observed events plus configuration: the
same trajectory always produces the same decisions. `microloop replay` relies on
this. There is no clock, randomness or network access in the core.

## Bounds and invariants

- Steps must strictly increase; duplicate or out-of-order steps are rejected
  without mutating history.
- The history window is bounded by `MonitorConfig.window` (`4..=4096`).
- A cached verification identity observed with a different failure count is
  rejected as conflicting.

## Bindings

The PyO3 bindings expose `Monitor`, `Policy` and `RuntimeController` as
JSON-in/JSON-out classes. The Pythonic surface (`microloop/__init__.py`) adds the
`Event` and `Decision` dataclasses, plus `InterventionAction` and `ProgressState`
as plain classes holding string constants, and helpers such as
`Decision.should_intervene` and `Decision.recovery_context`. The
`microloop.runtime` subpackage mirrors the Rust runtime types (`RuntimeState`,
`ProgressSnapshot`, `RuntimeDecision`, `Budget`, `Capabilities`) and adds the
host-side pieces: the `RuntimeAdapter` protocol and the `Episode` accumulator.
