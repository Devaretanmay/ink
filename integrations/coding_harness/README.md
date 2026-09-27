# Coding harness integration

A small agent loop whose runtime Microloop controls. The harness owns the task,
workspace, tools, tests and model call; Microloop owns progress, runtime state
and the adaptation decision.

```text
integrations/coding_harness/
├── harness.py    Task, CodingHarness, RunResult, SYSTEM_PROMPT, the tool loop
├── providers.py  Provider protocol, SimulatedCodingProvider, AnthropicProvider
└── tasks.py      a controlled, deterministic task set
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
from integrations.experiment.runner import (
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

Swap `_simulated_factory(...)` for `AnthropicProvider()` to run the same harness
against a real model. The provider needs the tool results, so the harness
returns each tool's output to the model as a proper `tool_result` block tied to
the `tool_use` id the API issued.
