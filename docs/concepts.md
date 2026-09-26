# Concepts

Microloop has five concepts. Everything else is implementation detail.

## Trajectory

The ordered sequence of steps an agent takes: what it did, what it observed,
and whatever structured context the host attaches. Microloop keeps a bounded
window of the most recent steps and never stores prompts or source code unless
the host puts them in an observation.

## Event

One step. See the [README](../README.md#how-it-works) for the signal
conventions (`metadata.success`, `metrics.exit_code`, `metrics.failures`,
`metadata.verifier`, and so on). Missing signals are treated as unknown.

## Progress

`ProgressState` is the public classification:

- `healthy` — progress, or no evidence of non-progress.
- `warning` — a suspicious signal that does not justify claiming failure.
- `stalled` — recurring failed actions, recurring errors, or a verified plateau.
- `regressing` — a verifier got objectively worse than the best prior result.

The classification is conservative: without evidence, the state stays `healthy`.

## Failure signals

Failure signals are internal detectors. They appear in `decision.reasons` for
debugging but are not the product surface:

| Reason                     | Meaning                                             |
|----------------------------|-----------------------------------------------------|
| `repeated_action_result`   | Same action, observation, outcome and state recurred |
| `normalized_repetition`    | The above, after masking volatile tokens            |
| `repeated_error`           | Same error signature across failed steps            |
| `state_stagnation`         | Fresh verifications report the same failure count   |
| `state_oscillation`        | Supplied environment state alternates A/B           |
| `regression`               | Failures increased versus the best prior measurement |

## Decision

`Decision` is what `Monitor.observe(...)` returns: `step`, `status`, `reasons`,
`evidence`, `intervention`, `severity`, `verified_progress` and `feedback`.

`severity` is a heuristic score in `0.0..=1.0`, not a calibrated probability.

## Intervention

`InterventionAction` is what the policy advises: `observe`, `replan` or `stop`.
The default policy only observes. Automatic `replan` or `stop` requires explicit
opt-in, a cooldown and a cap.

Microloop returns instructions. The host executes them. Microloop never edits
files, retries tools, or stops a process on its own.
