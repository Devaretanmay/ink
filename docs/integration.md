# Integration

Microloop is a library. You call it from your agent loop; it never calls you.

## Minimal integration

```python
from microloop import Monitor

monitor = Monitor()

for step in agent.steps():
    decision = monitor.observe(
        action=step.action,
        observation=step.result,
        state=step.state,
        metrics={"exit_code": step.exit_code},
    )

    if decision.should_intervene:
        agent.inject(decision.recovery_context)
```

The default policy is observation-only, so this is safe to ship before you
trust any automatic intervention.

## Driving the loop

Microloop does not own your agent loop, so you keep control of termination and of
what "recovered" means for your run. A complete host loop looks like this:

```python
from microloop import InterventionAction, Monitor, Policy

monitor = Monitor(policy=Policy(stalled=InterventionAction.Replan))
interventions = []
stopped = False
seen_issue = False
recovered = False

for step in agent.steps():
    decision = monitor.observe(
        action=step.action,
        observation=step.result,
        metrics={"exit_code": step.exit_code},
    )

    if decision.status != "healthy":
        seen_issue = True
    elif seen_issue:
        recovered = True

    if not decision.should_intervene:
        continue

    interventions.append((decision.step, decision.intervention))
    if decision.intervention == InterventionAction.Stop:
        stopped = True
        break
    agent.inject(decision.recovery_context)

print(interventions, stopped, recovered)
```

Everything above is ordinary host code: Microloop classifies and advises, and you
decide what to do about it.

## Live monitoring

Point `monitor` at a trajectory JSONL an agent is writing and follow it:

```bash
microloop monitor run.jsonl --follow
```

It prints each step's progress state, evidence, and any intervention, then a
run summary.

## Opting into recovery

```python
from microloop import InterventionAction, Monitor, Policy

policy = Policy(
    warning=InterventionAction.Observe,
    stalled=InterventionAction.Replan,
    regressing=InterventionAction.Stop,
    cooldown_steps=5,
    max_interventions=2,
)
monitor = Monitor(policy=policy)
```

- `cooldown_steps` is the minimum number of steps between interventions.
- `max_interventions` caps how many times a run can be steered.
- `stop_at_step` is a hard step budget that always yields `stop`.

## Providing good signals

Detection quality depends on the signals you attach. Prefer:

- `metrics.exit_code` for shell/tool steps.
- `metadata.error` with a stable error signature (strip volatile noise yourself
  if you already have a canonical location).
- `metadata.verifier` + `metadata.verification_id` + `metrics.failures` for
  test/verify steps. A `verification_id` must identify one *fresh* run; reusing
  an id marks the sample as cached and it will not count toward stagnation.
- `state` for an environment snapshot (git head, file hashes) to enable
  stagnation and oscillation detection.

## Reading decisions

```python
decision.status      # "healthy" | "warning" | "stalled" | "regressing"
decision.reasons     # internal detector names, for debugging
decision.intervention# "observe" | "replan" | "stop"
decision.feedback    # recovery prompt when intervening
```

When `decision.intervention` is not `observe`, `decision.feedback` contains a
formatted recovery signal you can inject directly into the agent context.

## Handling `replan` and `stop`

- `replan`: inject `decision.recovery_context` and let the agent change
  strategy. Do not re-run the same failing action unless new evidence exists.
- `stop`: end the run or escalate to a human. Microloop never stops a process.

## Rust

```rust
use microloop_core::{Event, InterventionAction, Monitor};

let mut monitor = Monitor::new();
for step in agent_steps {
    let mut event = Event::new(step.step, step.action, step.result);
    event.metrics = Some([("exit_code".to_string(), step.exit_code)].into_iter().collect());
    let decision = monitor.observe(event)?;
    if decision.intervention != InterventionAction::Observe {
        // steer the agent
    }
}
```

## Offline analysis

Record a trajectory as schema `0.3.0` JSONL and analyze it without rerunning the
agent:

```bash
microloop inspect run.jsonl
microloop replay run.jsonl --json
```

`inspect` reports detection only, so it reflects the runtime's default
observation-only policy. `replay` re-runs the *recorded events* through the
current engine and additionally shows the intervention a host policy would
choose. Neither one reproduces the original agent execution: no model is called
and no tools run.

A trajectory whose `schema_version` major version differs from the runtime's is
rejected with a compatibility error rather than analyzed on a guess. Re-record
the trajectory, or upgrade Microloop.

Scope: this check happens in the CLI, when a file is read. `schema_version` is
not a field of the runtime `Event` and is not validated by `Monitor.observe()`,
so a trajectory you build yourself is your responsibility to keep compatible.
The runtime is version-agnostic by design and will happily consume a payload
whose fields have changed meaning; the guard exists to catch that at the file
boundary, not inside the library.

`Event` also ignores unknown keys rather than rejecting them, so extra fields in
a trajectory record are dropped silently.
