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

You own the agent loop. `Monitor` classifies and advises; it never runs tools,
calls a model, or stops a process.

## CLI

```bash
microloop inspect trajectory.jsonl   # detection only, default observe-only policy
microloop replay trajectory.jsonl --json  # re-run recorded events through the engine
microloop monitor trajectory.jsonl --follow
microloop doctor
```

`replay` re-runs *recorded events* through the current engine. It does not
reproduce the original agent execution. A trajectory with an incompatible
`schema_version` major version is rejected rather than analyzed.

## Building

```bash
pip install maturin
maturin develop --manifest-path python/microloop/Cargo.toml
```

No external credentials or network connections are required. All trajectory
evaluation runs locally in Rust via PyO3.
