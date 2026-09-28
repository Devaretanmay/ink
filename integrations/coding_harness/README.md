# Coding harness integration

A small agent loop whose runtime Microloop controls. The harness owns the task,
workspace, tools, tests and model call; Microloop owns progress, runtime state
and the adaptation decision.

```text
integrations/coding_harness/
├── harness.py    Task, CodingHarness, RunResult, SYSTEM_PROMPT, the tool loop
├── providers.py  Provider protocol, SimulatedCodingProvider, AnthropicProvider
├── tasks.py      a controlled, deterministic task set
├── close_tasks.py  24-task close-call band plus validate()/calibrate sizing
└── real_tasks.py  two Python bugs with hidden verifier (build_real_tasks)
```

## Why a harness rather than a closed CLI

A harness gives complete experimental control: the same task, workspace, tools
and budget, with only the runtime policy differing. It is also provider-neutral,
so the controller can be measured offline with a deterministic provider and then
against a real one without changing anything above the provider.

## The three adaptations are real

* **replan** — the adapter queues a short message and the harness injects it into
  the transcript, so the next model turn sees "re-evaluate, do not repeat".
* **escalate/deescalate model** — the adapter owns the tier ladder; the harness
  reads `adapter.model` for the next turn. No model name lives in core.
* **compact context** — the adapter runs the deterministic compactor over the
  recorded segments and keeps what a resuming agent needs.

## The simulated provider is a model of an agent, not a detector

`SimulatedCodingProvider` is what makes the experiment run offline. The rule it
follows is that **behaviour is a property of the task, fixed before the run, and
identical in both arms.** The agent reacts to two things it can see in its own
transcript: verification failures, and instructions the user gave it after the
opening prompt. A replan is one of those instructions. That is the entire
mechanism by which Microloop can affect it, and it is the same mechanism a
person watching the terminal would have.

An earlier version instead looked for a Microloop-shaped phrase in the
transcript and applied the fix only once it found one. That made the static arm
structurally incapable of success, and the resulting comparison (0/40 against
40/40) was a measurement of the harness. The current design can lose, and does.

The agent also carries memory: pressure earned on an approach is local, and an
instruction that arrives before the agent has gathered evidence on its current
approach reads as an interruption, so it abandons that approach and loses the
ground it made. That is what lets an over-eager controller make a run worse, and
it is the failure mode the example in `examples/adaptive-coding-agent/` shows.

## Usage

```python
from integrations.coding_harness import (
    AgentBehaviour,
    Ceiling,
    CodingHarness,
    build_tasks,
)
from integrations.experiment.runner import (  # underscore helpers are internal
    DEFAULT_TIERS,
    _simulated_factory,
    _tier_by_model,
    build_session,
)

task = build_tasks(1)[0]
# Replace the behaviour to model a different kind of stuck agent.
task.behaviour = AgentBehaviour(patience=3, ceiling=Ceiling.Correct, settle=3)

session = build_session("adaptive")
provider = _simulated_factory(_tier_by_model(DEFAULT_TIERS))(task)
harness = CodingHarness(task=task, provider=provider, session=session)
result = harness.run()
print(result.success, result.steps, result.adaptations)
```

Swap `_simulated_factory(...)` for `AnthropicProvider()` or `GroqProvider()` to run
the same harness against a real model. Both need the tool results, so the harness
returns each tool's output to the model as a proper `tool_result` block tied to
the id the API issued.

## Real models

`GroqProvider` uses the standard library only, so the harness installs with nothing
beyond the runtime. It reads `GROQ_API_KEY` from the environment and never stores
it on the instance or writes it to the episode store.

```bash
export GROQ_API_KEY=...
python -m integrations.experiment --real --provider groq --model openai/gpt-oss-120b \
  --task-set real --tasks 2 --max-steps 6 --max-calls 25
```

`--model` pins one id to every tier. That is the honest way to ask whether the
*controller* helps: with the same model on every rung an escalation is a no-op,
so a difference between arms cannot be a better model.

`--max-calls` is a hard ceiling that raises rather than returning a partial
answer. A metered API and an agent loop are a bad pairing without one: the loop
decides how many turns to take and nothing in it knows what a turn costs. A run
that stops this way is recorded as `budget_exhausted`, which is deliberately not
the same as `failed`.

`build_real_tasks()` holds two Python bugs with a hidden verifier, for real-model
runs. `version-padding` is a control the model should solve in one attempt.
`dedupe-unhashable` is a task whose obvious first fix is wrong, so the model has
to read why the first attempt failed rather than pattern-match the traceback. A
task that can only be passed in one attempt cannot show whether an intervention
helped.

### One thing to preserve

Both providers rebuild the wire format from the transcript, reading identifiers
back out of it. An earlier version kept a queue of the most recent turn's ids,
which silently stripped the tool calls and results of every *earlier* turn. The
run still worked and the model simply stopped being able to see most of its own
conversation, which is the worst possible failure for a measurement. If you add a
provider, read the ids from the transcript; do not track them per turn.
