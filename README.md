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
[![PyPI](https://img.shields.io/pypi/v/microloop.svg)](https://pypi.org/project/microloop/)
[![License: MIT OR Apache-2.0](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-blue.svg)](#license)

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

Microloop is an in-process library. It observes execution and returns progress
state plus a recommendation. Your agent stays in control: the runtime never calls
a model, runs a tool, or ends a run on its own, and makes no network requests.

The more signals you attach, the sharper the estimate. A one-line integration
works; [`docs/integration.md`](docs/integration.md) shows how to add verifier
scope, failure counts and environment state when you have them.

## Docs

- [concepts](docs/concepts.md): the mental model
- [integration](docs/integration.md): wiring it into a host loop
- [cli](docs/cli.md): command reference
- [architecture](docs/architecture.md): engine internals

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

Dual licensed under [MIT](LICENSE-MIT) or [Apache-2.0](LICENSE-APACHE), at your
option.
