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

When your integration also reports runtime conditions (model, context, tokens,
cost), Microloop returns them alongside progress and a runtime recommendation.
When progress degrades, a deterministic controller evaluates the possible
actions -- `replan`, `escalate_model`, `compact_context` -- and picks the best
tradeoff for the run: a run under context pressure compacts, a run that already
burned a replan escalates, a run on a short budget declines an expensive
escalation. Bind an adapter with a `RuntimeSession` and Microloop applies the
adaptation, records whether it helped, and reports cost per successful task.

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

`agent` stands for whatever loop you already have. For runnable versions see
[`examples/`](../../examples).

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
[architecture](architecture.md) if you want them.

Each decision also carries `progress` (state, signals, `since_step`), `runtime`
(the conditions you reported) and `recommendation` (a `RuntimeAction` and its
reason). See [runtime primitives](concepts.md) and
[adaptations](concepts.md).

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

That uses the rule ladder. For the controller that scores candidates and picks
the best, use `ScoredController` -- the strategy is explicit, not a hidden
switch:

```python
from microloop import Capabilities, Monitor, ScoredController

monitor = Monitor(controller=ScoredController(
    stalled="replan",
    capabilities=Capabilities(replan=True, model_switch=True, context_compaction=True),
))
```

## CLI

```bash
microloop inspect run.jsonl
```

```
Microloop


  step 6   stall detected
           repeated action 3 times
           same error repeated 3 times

  step 7   progress resumed

  step 10  stall detected
           repeated action 5 times
           same error repeated 5 times
           test failures unchanged 5 times

Trajectory ended stalled after 10 steps.
```

Only transitions are shown. A step that is progressing and adds nothing is
skipped, and the events tell the story in order. Add `--verbose` for every step,
or `--json` for the exact representation.

`replay` is the same trajectory as a timeline, `explain` shows the runtime
recommendation and the conditions behind it, `monitor` follows a file as an agent
writes it, and `stats` summarizes the local episode store. `doctor` checks the
runtime. See [cli.md](cli.md).

## Real integrations and the first experiment

Provider code lives in [`examples/`](../../examples):
a coding harness whose task, workspace, tools and tests the harness owns, and an
OpenAI Agents SDK integration that registers the SDK's lifecycle hooks without
forking its runner. Both use a provider-neutral `TieredAdapter` that performs the
three adaptations for real: it injects a replan message, moves the model tier, and
compacts the transcript deterministically. Model names live only in the adapter.

Every run can be recorded to a local SQLite episode store -- one row per episode,
per adaptation and per candidate the scored controller considered -- and
summarized with `microloop stats`. The static-vs-adaptive experiment runs each
task twice and reports task success, cost per success, tokens per success and
steps per success:

```bash
python -m integrations.experiment                   # offline, deterministic
python -m integrations.experiment --sweep --tasks 40 # compare controller policies
python -m integrations.experiment --real            # needs provider keys (default --provider groq)
```

### The first result was negative

On the 40-task offline run, with the shipped default policy, the adaptive arm
**did worse than doing nothing** (18/40 against 21/40, paired per task). The
cause is mechanical: replanning on every stalled step interrupts an agent that
is slowly grinding toward a fix, so it abandons the approach it has barely tried
and runs out of budget. A policy that intervenes once and gets out of the way
finishes the same task in 14 steps instead of failing at 40.

No configuration cleared p<0.05, and the sweep says so in its own output rather
than naming a winner. The honest reading is that the experiment is **underpowered,
not that it has settled the question**. Two things did come out of it:

- The `improved` label on an adaptation is not evidence. 95% of replans were
  scored `improved` while the adaptive arm was losing, because progress recovers
  locally and the task still fails. `microloop stats` now reports success rates
  per action beside a control group instead.
- The task set was missing tasks where intervention timing actually decides the
  outcome, so a controller that intervened constantly scored the same as one that
  never intervened. Adding them changed the ranking of the policies entirely.

None of this is evidence about real models. See
[integration.md](integration.md).

### The first real-model run

Against `openai/gpt-oss-120b` on Groq, one model pinned to every tier so model
switching could not confound the comparison: both arms solved both tasks in three
or four steps, **zero adaptations fired**, and no run ever left the healthy state.

That establishes that the `--real` path works against a real API, and that the
controller stays out of the way of a run that is working -- the failure mode an
adaptive runtime is most likely to have. It establishes nothing about benefit:
two tasks, both arms 2/2, no signal to measure. Reaching a task hard enough for a
real model to stall is on the order of a hundred calls per arm.

```bash
export GROQ_API_KEY=...
python -m integrations.experiment --real --provider groq --model openai/gpt-oss-120b \
  --task-set real --tasks 2 --max-steps 6 --max-calls 25
```

`--max-calls` is a hard ceiling, not a suggestion, and a run stopped that way is
recorded as `budget_exhausted` rather than `failed`: running out of allowance and
running out of ideas are different facts.

## How it fits

Microloop is not the agent, and it does not know what your agent is trying to
do. It reads the steps you report and reports how they are going.

It is an in-process library. It never calls a model, runs a tool, or ends a run,
and it makes no network requests. Your agent stays in control.

The more signals you attach, the sharper the estimate. A one-line integration
works; [`integration.md`](integration.md) shows how to add verifier
scope, failure counts and environment state when you have them.

## Docs

- [Reduce a trajectory to one signal](concepts.md): the mental model, including runtime primitives
- [Wire Microloop into your agent loop](integration.md): four steps, from sending steps to attaching verifier signals
- [Analyze a trajectory from the command line](cli.md): `inspect`, `replay`, `explain`, `monitor`, `doctor`
- [How the engine computes progress](architecture.md): module map, data flow, cost

Point an agent at [`llms.txt`](../../llms.txt) for a machine-readable index.

[CONTRIBUTING.md](../../CONTRIBUTING.md) · [SECURITY.md](../../SECURITY.md) · [CODE_OF_CONDUCT.md](../../CODE_OF_CONDUCT.md)

## Evaluation

The reproducible evaluation harness lives in [`benchmarks/`](../../benchmarks/).
Published results will only include runs carrying real-provider provenance.

## Development

```bash
pip install -e '.[dev]'
make check
```

See [CONTRIBUTING.md](../../CONTRIBUTING.md).

## License

[Apache-2.0](../../LICENSE). See [NOTICE](../../NOTICE) for attribution.
