# Architecture

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
├── policy.rs         Policy: progress state -> recommendation
├── monitor.rs        Monitor: facade returning Decision
└── config.rs         MonitorConfig: detection bounds and their validation
```

The Python package `python/microloop` wraps the core through a thin PyO3 layer
(`python/microloop/src/lib.rs`) that marshals events and decisions as JSON. No
detection or policy logic lives in the bindings.

## Pipeline

```text
events
  -> trajectory features
  -> progress state
  -> recommendation
```

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
5. `Policy::evaluate` maps the state to an `InterventionAction`, applying the
   cooldown and cap. `Monitor` attaches the feedback text. Detectors have no say
   in this step, and the policy never sees them.
6. The `Decision` is returned. The runtime performs no I/O and executes nothing.

## Normalization

`canonical.rs` masks ANSI escapes, UUIDs, git/docker hashes, hex addresses,
timestamps, temp paths, PIDs and ephemeral ports, then collapses whitespace and
truncates to 256 bytes. Normalized comparison lets `normalized_repetition`
detect structurally identical steps whose volatile tokens differ. Error
signatures are the first non-empty line of an observation, normalized.

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

The PyO3 bindings expose `Monitor` and `Policy` as JSON-in/JSON-out classes. The
Pythonic surface (`microloop/__init__.py`) adds the `Event` and `Decision`
dataclasses, plus `InterventionAction` and `ProgressState` as plain classes
holding string constants, and helpers such as `Decision.should_intervene` and
`Decision.recovery_context`.
