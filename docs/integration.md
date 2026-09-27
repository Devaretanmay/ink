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

## 5. Report runtime conditions (optional)

Progress is one question. To let Microloop also recommend a runtime action, tell
it the conditions the step ran under:

```python
decision = monitor.observe(
    action=step.action,
    observation=step.result,
    runtime={
        "model": "sonnet",
        "context_tokens": 46_000,
        "context_limit": 64_000,
        "cost": 0.84,
    },
)

decision.progress.state              # "stalled"
decision.runtime.model               # "sonnet"
decision.recommendation.action       # "replan"
decision.recommendation.reason       # "trajectory_stalled"
```

Every `runtime` field is optional: `model`, `context_tokens`, `context_limit`,
`input_tokens`, `output_tokens`, `cost`, `elapsed_seconds`, `tool_calls` and
`remaining_budget`. Microloop never estimates prices; `cost` is whatever you
measured. Add a `Budget` to make it enforce limits, and `Capabilities` to say
which actions your host can actually perform:

```python
from microloop import Budget, Capabilities, Monitor

monitor = Monitor(
    policy=Policy(stalled=InterventionAction.Replan),
    budget=Budget(max_cost=5.0, max_seconds=900),
    capabilities=Capabilities(replan=True, model_switch=False),
)
```

An exhausted budget stops the run; an action your host cannot perform is never
recommended. Declaring capabilities is what unlocks the actuators: with only
`replan` capable, a stall replans; add `model_switch` and the run escalates when
a replan did not recover; add `context_compaction` and it compacts first when the
context window is under pressure. That ordering is the default `RuleController`
ladder.

### Score the candidates instead of taking the first rung

`RuntimeController` picks the first permitted rung. `ScoredController` scores
every permitted candidate and picks the best, so a run under context pressure
compacts even when a replan is also available, and a run that already burned a
replan escalates instead of repeating it:

```python
from microloop import Capabilities, Monitor, ScoredController

controller = ScoredController(
    stalled="replan",
    capabilities=Capabilities(replan=True, model_switch=True, context_compaction=True),
    min_benefit=0.15,
)
monitor = Monitor(controller=controller)
```

The strategy is explicit: `controller.strategy` is `"scored"`, and
`controller.config["strategy"]` matches. There is no hidden `"adaptive"` mode.
The controller stays deterministic and provider neutral; `min_benefit` is the
stability margin a new action must beat `continue` by, which keeps a model from
bouncing between two rungs.

### Apply adaptations with a session

Capabilities say what the adapter *could* do. To let Microloop actually apply an
adaptation, bind the adapter with a `RuntimeSession`:

```python
from microloop import Capabilities, Monitor, RuntimeAction, RuntimeController, RuntimeSession

controller = RuntimeController(
    stalled="replan",
    capabilities=Capabilities(replan=True, model_switch=True, context_compaction=True),
)
session = RuntimeSession(Monitor(controller=controller), adapter)

for step in agent.steps():
    decision = session.observe(action=step.action, observation=step.result)
    if decision.recommendation.action == RuntimeAction.Stop:
        break

session.finish("completed")
print(session.summary()["cost_per_success"])
```

The session reports runtime state, asks the controller, applies the recommended
action through the adapter, and records the result. Each adaptation is recorded
separately with whether it helped (`improved`, `no_change` or `regressed`) once
its evaluation window has passed; `session.summary()` carries the episode,
including cost per successful task. It never applies `stop`: termination stays
yours. Without a session, Microloop only recommends.

## 6. Real integrations and the experiment

Two integrations ship outside the package, so the core stays provider-neutral.

`integrations/coding_harness` is a small agent loop the harness owns end to end:
task, workspace, tools, tests, model call. `TieredAdapter` performs the three
adaptations for real. Swap `SimulatedCodingProvider` for `AnthropicProvider` to
run the same harness against a real model.

`integrations/openai_agents` sits alongside the Agents SDK runner by registering
its lifecycle hooks; nothing about the runner is forked.

Both record into a local episode store:

```python
from microloop.store import EpisodeStore

store = EpisodeStore(".microloop/episodes.db")
store.record(result.episode, task=task.name, arm="adaptive", run_mode="real", success=result.success)
```

`run_mode` is `real` for actual model calls and `simulated` for the offline
agent model; the two are never mixed. `microloop stats` reads the store. The
static-vs-adaptive comparison lives in `integrations/experiment`:

```bash
python -m integrations.experiment                    # offline, deterministic
python -m integrations.experiment --sweep --tasks 40  # compare controller policies
python -m integrations.experiment --real --tasks 40   # Anthropic, needs keys
```

Microloop must be allowed to lose: if the adaptive arm does worse, the report
says so. That is the finding, not a bug to tune away.

### What the offline experiment found

On 40 tasks, with the shipped default policy, the adaptive arm did **worse**
than doing nothing. The comparison is paired, so this is a real swing rather
than a difference in task difficulty:

| policy | success | won | lost | p |
|---|---|---|---|---|
| static (no adaptation) | 21/40 | — | — | — |
| eager replan | 18/40 | 0 | 3 | 0.250 |
| patient replan | 22/40 | 1 | 0 | 1.000 |
| wary replan | 16/40 | 0 | 5 | 0.062 |

Replanning on every stalled step interrupts an agent that is slowly grinding
toward a fix. The agent abandons the approach it has barely tried, loses the
ground it made, and runs out of budget.

Read the p-values before the counts. None of these clear 0.05, and the sweep
says so in its own output: with at most five tasks where any policy disagreed
with the baseline, one more discordant task would be needed before a sign test
could reach significance at all. **The honest reading is that this experiment is
underpowered, not that it has settled the question.** It does rule out a large
effect in either direction, and it does show that intervening as fast as a run
stalls is not free.

Two things follow. The `improved` label on an adaptation is not a useful
measure of whether it helped: 95% of replans were scored `improved` while the
adaptive arm was losing, because progress recovers locally and the task still
fails. `EpisodeStore.attribution()` reports success rates instead, beside a
control group of runs with no adaptation. And the task set needs more close
calls, where the outcome is actually decided by intervention timing, before any
of these rows means much.

None of this is evidence about real models. The offline agent is a model of an
agent, described in `integrations/coding_harness/tasks.py`; the `--real` path is
the one that would say something about them.

### The first real-model run was null

Against `openai/gpt-oss-120b` on Groq, one model pinned to every tier so that
model switching could not confound the comparison:

| task | arm | success | steps | adaptations |
|---|---|---|---|---|
| version-padding | static | yes | 4 | 0 |
| version-padding | adaptive | yes | 3 | 0 |
| dedupe-unhashable | static | yes | 3 | 0 |
| dedupe-unhashable | adaptive | yes | 4 | 0 |

Zero adaptations, and no run left the healthy state. The model solved both tasks
in three or four steps, so there was nothing to detect.

What that does and does not mean:

- The `--real` path works against a real API: real tool calls, tool results
  returned with their identity intact, a real multi-turn conversation, and an
  episode persisted per arm.
- The controller is calibrated in the narrow sense that matters most: it stayed
  out of the way of a run that was working. Spurious intervention is the failure
  mode an adaptive runtime is most likely to have, and this run does not have it.
- It says **nothing** about whether adaptation helps. Two tasks, both arms 2/2,
  zero interventions. There is no signal to measure.

The limit is the call budget. Reaching a task hard enough for a real model to
stall, let alone stall enough for the detectors to fire, is on the order of a
hundred calls per arm. Treat this as plumbing that is now proven and calibration
that is now observed, not as a result.

```bash
export GROQ_API_KEY=...
python -m integrations.experiment --real --provider groq \
  --model openai/gpt-oss-120b --task-set real --tasks 2 \
  --max-steps 6 --max-calls 25
```

`--max-calls` is a hard ceiling. Exceeding it raises rather than returning a
partial answer, because a metered API and an agent loop are a bad pairing without
one: the loop decides how many turns to take and nothing in it knows what a turn
costs. A run that stops this way is recorded as `budget_exhausted`, which is
deliberately not the same as `failed`.

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
