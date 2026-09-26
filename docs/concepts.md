# Concepts

Microloop observes a sequence of agent steps and reduces it to one question: is
the run still making progress?

```text
Event  ->  Trajectory  ->  Progress state  ->  Decision
```

Everything else is implementation detail.

## Event

One agent step. It carries what the agent did, what it observed, and whatever
structured context you attach:

```python
Event(
    step=12,
    action="pytest tests/",
    observation="4 failed, 2 passed",
    state={"git_head": "1a2b3c4"},        # environment snapshot
    metrics={"exit_code": 1, "failures": 4},
    metadata={"verifier": "pytest", "verification_id": "run-12"},
)
```

Only `step`, `action` and `observation` are required. The rest is optional, and
the more you supply the sharper the estimate. See
[integration](integration.md#4-attach-stronger-signals-when-you-have-them) for the full set of
conventions.

## Trajectory

The recent sequence of events. Microloop keeps a bounded window of the most
recent steps and forgets the rest, so memory use is bounded regardless of how
long a run gets.

It stores what you give it. Microloop does not read your prompts, your source
files, or anything else you did not put in an event.

## Progress

Microloop's current view of whether execution is advancing, as one of four
states:

| State | |
|---|---|
| `healthy` | Progress, or no evidence otherwise. |
| `warning` | A signal worth noticing that does not yet justify a stronger read. |
| `stalled` | Recurring failures, recurring errors, or a verification result that has stopped moving. |
| `regressing` | A verifier got worse than the best result seen so far in the same scope. |

The classification is conservative. Without evidence, the state stays `healthy`.

### Progress signals

State is derived from four things an agent already produces:

- **Recurrence.** The same action, observation and environment state repeating,
  either exactly or after volatile tokens such as paths, hashes, timestamps and
  PIDs are masked out.
- **Verification movement.** Whether a verifier's result is improving, flat or
  worse than its own best, compared within one scope.
- **Environment state.** Whether supplied state is holding still or oscillating.
- **Errors.** Whether the same error signature keeps coming back.

Each of these is an internal detector, surfaced in `decision.reasons` for
debugging. They are not the product surface, and they may change between
releases. The abstraction that matters is the progress state they add up to.

## Decision

What `Monitor.observe(...)` returns:

```python
decision.status            # the state
decision.evidence          # which steps it came from, and why
decision.reasons           # which detectors fired, for debugging
decision.intervention      # "observe" | "replan" | "stop"
decision.verified_progress # a verifier reported an improvement
decision.feedback          # prompt to inject, set only when recommending action
```

`evidence` is the part worth reading. Every entry names the steps behind it, so
a decision can be traced back to the trajectory that produced it.

Branch on `status`. Do not branch on the numeric `severity` field: it is a fixed
lookup over `status` and carries no information beyond it.
