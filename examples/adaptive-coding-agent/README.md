# Adaptive coding agent

The same task, run static and adaptive, on two tasks chosen because they
disagree about the answer. Fully offline and deterministic, so it is safe to run
in CI.

```bash
python examples/adaptive-coding-agent/agent.py
```

## What it shows

**A task the agent solves unaided.** Both arms finish, and the adaptive arm paid
for the privilege: it performed an adaptation and gained nothing. This is the most
common outcome in the real 40-task sweep, and it is worth seeing because it is
invisible in an aggregate success rate.

**A close call.** Here the timing of the intervention decides the run:

```text
static            -> success=True   steps=38  adaptations=0
adaptive (eager)  -> success=False  steps=40  adaptations=17
adaptive (wary)   -> success=True   steps=14  adaptations=1
```

The eager policy replans on every stalled step. Against an agent that is slowly
grinding toward the fix, each replan reads as an interruption: the agent abandons
the approach it has barely tried, loses the ground it made, and runs out of
budget. It ended on rung 1 of 4 having been knocked back eight times.

The wary policy intervenes once and gets out of the way, and the agent finishes
in 14 steps.

Intervening *more* made the task worse and *more expensive* at the same time.
That is the result worth knowing, and it is why
`python -m integrations.experiment --sweep` reports every controller policy
rather than only the best one.

## Not a result

This example uses a simulated agent whose behaviour is described in
`integrations/coding_harness/tasks.py`. It demonstrates that the adapters and
the controller are wired to a real agent loop. It is not evidence about real
models, and it is not presented as any.

For the full experiment, including the sign test on the paired comparison:

```bash
python -m integrations.experiment --sweep --tasks 40
python -m integrations.experiment --real --tasks 40   # needs an API key
```
