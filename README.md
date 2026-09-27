# Microloop

### Runtime progress for autonomous agents.

Agents can keep running long after they stop getting anywhere.

Microloop watches an execution trajectory and reduces it to one question: is this
run still advancing?

```text
healthy  ->  warning  ->  stalled  ->  regressing
```

Feed it the actions, observations and state your agent already produces.
Microloop runs in process and returns the current progress state, the evidence
behind it, and, if you enable it, a recommendation for what the host should do
next.

[![CI](https://github.com/Devaretanmay/microloop/actions/workflows/ci.yml/badge.svg)](https://github.com/Devaretanmay/microloop/actions)
[![PyPI](https://img.shields.io/pypi/v/microloop.svg?v=0.3.0)](https://pypi.org/project/microloop/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](#license)

## Install

```bash
pip install microloop
```

Python 3.10 to 3.13. Wheels for Linux, macOS and Windows.

The Rust crate is not on crates.io yet, so there is no `cargo add` line. Build it
from a checkout with a path dependency:

```toml
[dependencies]
microloop-core = { path = "../microloop/crates/microloop-core" }
```

## Use

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

    print(decision.status, decision.reasons)

    if decision.should_intervene:
        agent.inject(decision.recovery_context)
```

`agent` stands for whatever loop you already have. For a runnable version see
[`examples/coding-agent`](examples/coding-agent).

## Why not just put this in a prompt?

Because a prompt has neither of the two things this needs. It has no memory
across steps, so it cannot see that step 29, 30 and 31 were the same command.
And it does not see your verifier results, so it cannot tell a plateau from a
plateau that is quietly getting worse. Microloop reads what the run already
produces and keeps the last 32 steps in a fixed window.

## Cost

Per call to `observe()`, window full and every detector running. Python 3.13,
Apple M4, release build, 100,000 steps after 5,000 warmup:

| Traffic | median | p99 |
|---|---|---|
| healthy, distinct commands | 48 µs | 56 µs |
| a verifier reporting improvement | 92 µs | 103 µs |
| the same test failing repeatedly | 112 µs | 122 µs |

Memory is flat: 1 KiB traced after 100,000 steps, because the window holds 32
records regardless of run length. No runtime Python dependencies, three Rust
ones, 558 KiB compressed wheel.

Absolute timings are machine-specific. Re-derive them with `make perf`.

## What you get back

Every call returns a `Decision`:

| Field | |
|---|---|
| `status` | `healthy`, `warning`, `stalled` or `regressing` |
| `evidence` | which steps the state came from, and why |
| `reasons` | which detectors fired, for debugging |
| `intervention` | `observe`, `replan` or `stop` |
| `verified_progress` | a verifier reported an improvement |
| `feedback` | a prompt to inject, set only when recommending action |

Progress state is derived from recurrence between steps, movement in
verification results, environment state, and repeated errors. The internal
detectors are an implementation detail; see
[architecture](docs/architecture.md) if you want them.

By default the runtime only observes. Recommendations require an explicit
policy:

```python
from microloop import InterventionAction, Monitor, Policy

monitor = Monitor(policy=Policy(
    stalled=InterventionAction.Replan,
    regressing=InterventionAction.Stop,
    cooldown_steps=5,
))
```

## CLI

```bash
microloop inspect run.jsonl
```

```
Microloop trajectory analysis (schema 0.3.0)
Steps          10
Status         stalled
Detected at    step 6
Reasons        repeated_action_result, repeated_error, state_stagnation
Evidence       Same action, observation and supplied state recurred
Action         observe (default policy: observe only)
```

`replay`, `monitor` and `doctor` cover offline replay, live inspection and
runtime checks. See [docs/cli.md](docs/cli.md).

## How it fits

Microloop is not the agent, and it does not know what your agent is trying to
do. It reads the steps you report and reports how they are going.

It is an in-process library. It never calls a model, runs a tool, or ends a run,
and it makes no network requests. Your agent stays in control.

The more signals you attach, the sharper the estimate. A one-line integration
works; [`docs/integration.md`](docs/integration.md) shows how to add verifier
scope, failure counts and environment state when you have them.

## Docs

- [Reduce a trajectory to one signal](docs/concepts.md): the mental model
- [Wire Microloop into your agent loop](docs/integration.md): four steps, from sending steps to attaching verifier signals
- [Analyze a trajectory from the command line](docs/cli.md): `inspect`, `replay`, `monitor`, `doctor`
- [How the engine computes progress](docs/architecture.md): module map, data flow, cost

Point an agent at [`llms.txt`](llms.txt) for a machine-readable index.

[CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

## Evaluation

The reproducible evaluation harness lives in [`benchmarks/`](benchmarks/).
Published results will only include runs carrying real-provider provenance.

## Development

```bash
pip install -e '.[dev]'
make check
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache-2.0](LICENSE). See [NOTICE](NOTICE) for attribution.
