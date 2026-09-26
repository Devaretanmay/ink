# Microloop

### Keep agents making progress.

[![CI](https://github.com/Devaretanmay/microloop/actions/workflows/ci.yml/badge.svg)](https://github.com/Devaretanmay/microloop/actions)
[![License: MIT OR Apache-2.0](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-blue.svg)](#license)

Microloop is a local reliability runtime for autonomous agents. It watches an
agent's execution trajectory, detects when the agent is looping, stalled,
regressing, or operating on stale state, and can trigger a configured
intervention when progress degrades.

Workflow engines keep agents *running*. Observability tools show *what agents
did*. Guardrails decide *what agents may do*. Microloop decides *whether the
agent is still making useful progress*.

The runtime is **low-overhead and in-process with no required network calls**.
It sits under any harness. It is not an agent framework.

---

## Installation

Not yet on PyPI — the `microloop` name is held by an unrelated project, so the
distribution name is unresolved. Until it ships, build from source:

```bash
git clone https://github.com/Devaretanmay/microloop
cd microloop
pip install -e '.[dev]'
maturin develop --manifest-path python/microloop/Cargo.toml
```

Rust — not yet on crates.io. The publish job is wired up in the release
workflow, so this becomes available on the next tag:

```toml
# Cargo.toml
[dependencies]
microloop-core = "0.3"
```

## Quick start

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

You own the agent loop. Microloop classifies and advises; it never calls a model,
runs a tool, or stops a process.

By default the runtime only observes. Automatic recovery requires an explicit
policy:

```python
from microloop import InterventionAction, Monitor, Policy

policy = Policy(
    stalled=InterventionAction.Replan,
    regressing=InterventionAction.Stop,
    cooldown_steps=5,
)
monitor = Monitor(policy=policy)
```

`Monitor.observe(...)` returns a `Decision` with `status`, `reasons`,
`intervention` and `severity`:

```python
Decision(step=31, status="stalled", reasons=["state_stagnation", "repeated_error"],
         intervention="replan", severity=0.8, verified_progress=False, feedback="...")
```

`severity` is a fixed lookup over `status` (`0.0`/`0.4`/`0.8`/`0.9`). It is not a
probability and not a confidence — branch on `status`.

## What Microloop detects

The public API exposes four progress states:

| Status       | Meaning                                                        |
|--------------|----------------------------------------------------------------|
| `healthy`    | Progress, or no evidence of non-progress.                      |
| `warning`    | Suspicious signal that is not enough to claim failure.         |
| `stalled`    | Recurring failed actions, errors, or a verified plateau.       |
| `regressing` | A verifier got objectively worse than the best prior result.   |

Individual detectors (`repeated_action_result`, `normalized_repetition`,
`repeated_error`, `state_stagnation`, `state_oscillation`, `regression`) are
internal implementations surfaced in `decision.reasons` for debugging. They are
not the product surface.

## How it works

```
EVENT -> TRAJECTORY -> PROGRESS ENGINE -> DECISION -> POLICY -> INTERVENTION
```

`Monitor.observe(event)` performs detection; the attached `Policy` maps a
progress state to an intervention (`observe`, `replan`, `stop`). The runtime
returns instructions only. The host decides whether to act.

Signals are read by convention from each event:

| Key                          | Meaning                              |
|------------------------------|--------------------------------------|
| `metadata.success`           | `"false"`/`"0"` marks a failed step  |
| `metadata.error`             | stable error signature               |
| `metadata.verifier`          | verifier scope name                  |
| `metadata.verification_id`   | unique id of one fresh verification  |
| `metrics.exit_code`          | non-zero marks a failed step         |
| `metrics.failures`           | verifier failure count               |

Missing signals are treated as unknown. The runtime never invents evidence.

## CLI

```bash
microloop inspect trajectory.jsonl
microloop replay trajectory.jsonl --json
microloop monitor trajectory.jsonl --follow
microloop doctor
```

```
$ microloop inspect tests/fixtures/sample_trajectory.jsonl
Microloop trajectory analysis (schema 0.3.0)
Steps          10
Status         stalled
Detected at    step 6
Reasons        repeated_action_result, repeated_error, state_stagnation
Evidence       Same action, observation and supplied state recurred
Action         observe (default policy: observe only)
```

`inspect` reports detection under the default observation-only policy, so what it
shows is what a default runtime would do. `replay` and `monitor` additionally
show the intervention a host policy *would* choose, labelled as a
recommendation. Neither command reproduces the original agent execution: no model
is called and no tools run. A trajectory whose `schema_version` major version
does not match the runtime is rejected with a compatibility error.

`microloop monitor` prints a live progress view and, with `--follow`, keeps
reading as a running agent appends steps:

```
Microloop
trajectory run.jsonl (following)

   3  STALLED    repeated_action_result repeated_error
      Same action, observation and supplied state recurred
      -> REPLAN (recommended)

completed
Steps             10
Stalls            3
Recommended       3
Recovered         yes
```

## Integration

Microloop is a library with no required network calls and no opinions about your
harness. It sits under any loop that can call `observe(...)` once per step. See
[`docs/integration.md`](docs/integration.md) for the integration pattern, and
[`examples/coding-agent`](examples/coding-agent) for a complete offline
recovery example.

## Benchmarks

On a held-out SWE-bench Verified evaluation (`validation-final-v1`, 100 paired
tasks, frozen manifest, `gpt-6-astra`, mini-swe-agent v2.4.6), adding Microloop
to the same agent improved completion from 51/100 to 63/100 while reducing
median tool calls by 21.3%:

| Metric            | Vanilla | Microloop | Difference   |
|-------------------|---------|-----------|--------------|
| Tasks solved      | 51/100  | 63/100    | +12          |
| 95% paired CI     | —       | —         | [+5.0,+20.0] pp |
| Median tool calls | 61      | 48        | −21.3%       |

`benchmarks/` contains the reproducible methodology and runner. Published
summaries live under `benchmarks/results/published/`.
[Methodology →](benchmarks/README.md)

## Architecture

- [`docs/concepts.md`](docs/concepts.md) — trajectory, progress, decisions, interventions
- [`docs/integration.md`](docs/integration.md) — integrating Microloop
- [`docs/architecture.md`](docs/architecture.md) — engine internals, normalization, bindings

## Development

```bash
pip install -e '.[dev]'
make check   # fmt, clippy, ruff, cargo test, pytest, the example, and a wheel build
make test    # cargo test + pytest
make build   # release build
```

`make check` is the same gate the release workflow runs before it publishes
anything. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Dual-licensed under [MIT](LICENSE-MIT) or [Apache-2.0](LICENSE-APACHE), at your
option.
