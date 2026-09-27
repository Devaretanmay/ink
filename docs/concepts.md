# Reduce a trajectory to one signal

An agent can run for a long time and stop getting anywhere. Microloop reads the
steps and reports whether the run is still advancing.

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

## Runtime

Progress answers *is this run advancing?* The runtime layer answers a second
question: *under what conditions is it advancing, and what should change?*

Two additions carry that distinction.

A `RuntimeState` is the optional execution condition you report with a step:
model, context usage, tokens, cost, elapsed time and remaining budget. All of it
is optional, and you can adopt it incrementally:

| Level | You send | Microloop can |
|---|---|---|
| `signals` | `action`, `observation` | detect repetition and repeated errors |
| `progress` | plus `state`, `metrics`, `metadata` | measure actual progress |
| `runtime` | plus `runtime` | recommend runtime actions |

```python
decision = monitor.observe(
    action="pytest tests/",
    observation=output,
    runtime={"model": "sonnet", "context_tokens": 46_000, "context_limit": 64_000, "cost": 0.84},
)
```

A `ProgressSnapshot` is Microloop's clean read of progress for the step: the
`state`, the detector `signals`, the `step`, the `since_step` at which the
current state began, and any verifier `verification_delta`.

A `RuntimeDecision` is what the runtime controller recommends from the snapshot
and the runtime state:

```python
decision.progress.state            # "stalled"
decision.progress.since_step       # 31
decision.runtime.model             # "sonnet"
decision.runtime.context_utilization  # 0.72
decision.recommendation.action     # "replan"
decision.recommendation.reason     # "trajectory_stalled"
```

The full action vocabulary is defined now (`continue`, `replan`,
`escalate_model`, `deescalate_model`, `compact_context`, `restore_checkpoint`,
`retry_tool`, `branch_strategy`, `stop`). Six are produced today; checkpointing,
tool retry and branching remain experimental. A `RuntimeController` never
recommends an action the adapter does not declare through `Capabilities`, and an
exhausted `Budget` always stops the run.

None of this changes the default. A `Monitor` with no configuration observes.

### Adaptations

Opting a state into an actuator turns recommendations on. The controller no
longer takes the first rule that matches; it evaluates the possibilities:

```text
progress + runtime + controller history
        -> candidate actions   (rules)
        -> gating              (capabilities, availability, budget, cooldown, cap)
        -> scoring             (expected progress, cost, repetition, pressure)
        -> best action
```

`rules.candidates()` still decides what is *possible*: the full vocabulary
(`continue`, `stop`, `compact_context`, `replan`, `escalate_model`,
`deescalate_model`). Gating removes anything the adapter cannot perform, that
the host did not make available this step, that is on cooldown, or that the run
has spent its intervention cap on. What survives is scored, and the highest
score wins -- unless it fails to beat staying the course by a stability margin,
in which case the controller recommends `continue`. Ties break in a fixed order,
so the choice is reproducible.

Two strategies share that pipeline, and which one is in use is explicit.
`RuntimeController` keeps the Pass 2 rule ladder (`RuleController`): on a stall it
walks `compact_context` when the context is under pressure, then `replan`, then
`escalate_model`, and de-escalates after sustained progress. `ScoredController`
scores every permitted candidate and picks the best. Neither is a hidden switch:
the strategy is a field you set and can inspect.

A score combines four small, readable terms:

| Dimension | What it captures |
|---|---|
| Expected progress | How likely the action is to help given the state and what has already been tried |
| Cost | The controller's relative cost for the action; escalation is expensive, de-escalation negative |
| Repetition | Whether this action has already failed inside the current stall episode |
| Runtime pressure | Context utilization, remaining budget, and sustained progress |

The pressure term is what makes the choices adaptive: under a nearly exhausted
budget, escalation is penalised; above the context threshold, compaction is
boosted; after sustained progress under a costlier model, de-escalation is
boosted. When every actuator has been tried and failed, the controller stops
cycling and reports `action_exhausted`, recommending `continue`.

Escalation is a *direction*, not a model name: the adapter owns the ladder, so
Microloop stays provider neutral. Each candidate is bounded by a cooldown and the
per-run cap. See [integration](integration.md#5-report-runtime-conditions) for
how to apply them.

### Action outcomes and stall episodes

Choosing an action is only half the loop. After an action is applied, the
controller waits a short evaluation horizon (`replan` 3 steps, `compact_context`
2, the model changes 3) and then compares progress before and after:

| Outcome | |
|---|---|
| `improved` | the progress state moved forward |
| `no_change` | the state is unchanged |
| `regressed` | the state got worse |

`improved` means progress improved *after* the action, not that the action
caused success; keeping that distinction is what makes the eventual learning
loop honest.

The actions attempted while a run is stuck belong to one `StallEpisode`, opened
when progress leaves `healthy` and closed when it recovers. It remembers what was
tried and how it turned out, which is what lets the controller penalise a failed
action instead of oscillating between two of them.

### Trace

Every scored decision carries a `ControllerTrace`: the candidates considered,
their scores, and the selected action. It is deliberately out of the default
view. `microloop replay --verbose` renders it and `--json` includes it, so normal
output stays short while the reasoning is available when you need it.

## Episode

One agent task is an `Episode`: its goal, progress transitions, runtime changes,
the adaptations tried, the outcome and the usage. Each `AdaptationRecord`
carries not only what was attempted but whether it helped (`result` is
`improved`, `no_change` or `regressed`, once the window has passed) and whether
the adapter actually performed it. This is the unit later passes will learn from
and report on; nothing is trained from it yet.

## Real integrations and outcome collection

Two things turn the vocabulary above into a working runtime.

A **`TieredAdapter`** is the provider-neutral adapter: it holds a tier-to-model
map (`ModelTier.Fast`/`Balanced`/`Strong`), a transcript of labelled segments, and
a deterministic `ContextCompactor`. It performs the three adaptations for real --
injects a replan message, moves the model tier, compacts the transcript -- while
Microloop only ever recommends the direction. Provider-specific adapters
(`integrations/openai_agents`, `integrations/coding_harness`) subclass or build
on it and live outside the package.

An **`EpisodeStore`** is a local SQLite file that records one row per episode,
one per adaptation and one per candidate the scored controller considered. An
`AdaptationResult` is the full before/after record of one action, and a
`TaskOutcome` is the host's verdict on the whole task (`success`, `verifier`,
`score`) -- the ground truth that action outcomes are eventually judged against.
Nothing is trained from any of it; the point is to be able to ask, after a few
hundred runs, which adaptations actually helped and under what conditions.

```python
from microloop.store import EpisodeStore

store = EpisodeStore(".microloop/episodes.db")
store.record(session.episode, task=task.name, arm="adaptive", success=result.success)
```

`microloop stats` summarizes the store; see [cli](cli.md#stats).
