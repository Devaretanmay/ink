# Microloop Python SDK

Python bindings for the Microloop local reliability runtime.

```python
from microloop import InterventionAction, Monitor, Policy

policy = Policy(
    stalled=InterventionAction.Replan,
    regressing=InterventionAction.Stop,
    cooldown_steps=5,
)
monitor = Monitor(policy=policy)

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

`Monitor.observe(...)` returns a `Decision` with `status`, `reasons`,
`intervention`, `severity`, `verified_progress` and `feedback`. The default
policy only observes; automatic recovery requires explicit opt-in.

Or wrap the agent and let Microloop drive the loop:

```python
import microloop

agent = microloop.wrap(agent, policy=policy)
report = agent.run(task)
print(report.status, report.interventions, report.recovered)
```

## CLI

```bash
microloop inspect trajectory.jsonl
microloop replay trajectory.jsonl --json
microloop monitor trajectory.jsonl --follow
microloop doctor
```

## Building

```bash
pip install maturin
maturin develop --manifest-path python/microloop/Cargo.toml
```

No external credentials or network connections are required. All trajectory
evaluation runs locally in Rust via PyO3.
