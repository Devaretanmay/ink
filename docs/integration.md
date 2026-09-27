# Wire Microloop into your agent loop

Four steps. The first is enough to get value; the rest make it sharper.

## 1. Send steps

Call `observe` once per step, with whatever the step gives you.

```python
from microloop import Monitor

monitor = Monitor()

for step in agent.steps():
    decision = monitor.observe(
        action=step.action,
        observation=step.result,
    )
```

Nothing else is required. A monitor with no configuration only observes.

## 2. Read progress

```python
print(decision.status)     # healthy | warning | stalled | regressing
print(decision.evidence)    # which steps, and why
```

`status` is the signal. `evidence` explains it: each entry names the steps that
contributed, so you can log or surface a trace of how a run got where it is.

## 3. Act on recommendations, if you want to

The default monitor is read-only. Enable recommendations explicitly when you want
the host agent to act on them:

```python
from microloop import InterventionAction, Monitor, Policy

monitor = Monitor(policy=Policy(
    stalled=InterventionAction.Replan,
    regressing=InterventionAction.Stop,
))
```

When a recommendation is not `observe`, `decision.feedback` holds a prompt you
can inject into the agent's context. It lists the evidence and asks the agent to
re-evaluate.

You keep control of termination. Nothing ends a run unless your code does:

```python
if decision.intervention == InterventionAction.Stop:
    break
```

## 4. Attach stronger signals when you have them

Progress state is only as sharp as the signals behind it. All optional.

| Key | |
|---|---|
| `metrics.exit_code` | marks a failed step |
| `metadata.error` | a stable error signature, so repeated errors are recognised as one error |
| `metadata.verifier` | scope name, e.g. `"pytest:auth"`, so results are only compared within one scope |
| `metadata.verification_id` | unique per run of that verifier, so a cached result is not mistaken for a fresh one |
| `metrics.failures` | failure count, which is what makes plateau and regression detection possible |
| `state` | environment snapshot (git head, file hashes), which enables holding-still and oscillation detection |

```python
decision = monitor.observe(
    action="pytest tests/ -x",
    observation=output,
    state={"git_head": head},
    metrics={"exit_code": 1, "failures": 4},
    metadata={
        "verifier": "pytest",
        "verification_id": f"run-{n}",
        "error": "AssertionError: test_admin.py:42",
    },
)
```

Two conventions worth knowing. A `verification_id` must identify one *fresh*
run: reusing an id marks the sample as cached, and cached samples do not count
toward a plateau. And signals Microloop does not recognise are treated as
unknown, never as evidence of anything.

## Advanced configuration

A `Policy` bounds how often the host can be steered:

| Option | Default | |
|---|---|---|
| `cooldown_steps` | `5` | minimum steps between recommendations |
| `max_interventions` | `2` | cap per run |
| `stop_at_step` | `None` | hard step budget, always yields `stop` |
| `healthy` / `warning` / `stalled` / `regressing` | `observe` | per-state recommendation |

`Monitor(...)` tunes the estimate itself: `window` (32), `repetitions` (3),
`stagnation_steps` (8), `verification_samples` (3), `normalization` (`True`).

## Host bookkeeping

Tracking recovery is ordinary host code, and you decide what it means for your
run:

```python
seen_issue = False
for step in agent.steps():
    decision = monitor.observe(action=step.action, observation=step.result)
    if decision.status != "healthy":
        seen_issue = True
    elif seen_issue:
        print("recovered")
        seen_issue = False
```

## Rust

```rust
use microloop_core::{Event, InterventionAction, Monitor};

let mut monitor = Monitor::new();
for step in agent_steps {
    let mut event = Event::new(step.step, step.action, step.result);
    event.metrics = Some([("exit_code".to_string(), step.exit_code)].into_iter().collect());
    let decision = monitor.observe(event)?;
    if decision.intervention != InterventionAction.Observe {
        // steer the agent
    }
}
```

## Offline

Record a trajectory as schema `0.3.0` JSONL and analyze it later, without
rerunning the agent. See [cli](cli.md).

A trajectory whose `schema_version` major version differs from the runtime's is
rejected with a compatibility error rather than analyzed on a guess. That check
happens in the CLI, when a file is read: `schema_version` is not a field of the
runtime `Event`, and `Monitor.observe()` is version-agnostic by design. A
trajectory you build yourself is yours to keep compatible.
