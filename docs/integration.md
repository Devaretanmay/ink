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

## Wrapping an agent

If you would rather not call `observe` yourself, wrap the agent. Microloop then
drives the loop, forwarding recovery context and honoring `stop`:

```python
import microloop
from microloop import Policy

agent = microloop.wrap(agent, policy=Policy(stalled="replan", regressing="stop"))
report = agent.run(task)

print(report.status)          # final progress state
print(report.interventions)   # [(step, action), ...]
print(report.recovered)       # returned to healthy after a stall?
print(report.stopped)         # did a stop intervention end the run?
```

The wrapped agent must be iterable as steps: either directly, callable with the
task, or exposing `run(task)` that yields steps. Each step is a mapping or an
object with `action` and `observation` (plus optional `state`, `metrics`,
`metadata`, `step`). Pass `adapter=...` to map a custom step type. When the
agent exposes `inject(...)` or `steer(...)`, the recovery context is forwarded.

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
