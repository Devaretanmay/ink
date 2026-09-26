# Coding agent example

A genuine, offline integration: a small agent fixes a buggy function while
Microloop watches the trajectory.

The agent starts with a wrong fix strategy. Because it repeats the same failed
edit and the same assertion, Microloop classifies the trajectory as `stalled`
and returns a `replan` intervention. The agent then switches strategy and the
test passes.

```bash
pip install microloop
python agent.py
```

Expected shape:

```
 1  healthy    observe  -
 2  healthy    observe  -
 3  stalled    replan   repeated_action_result repeated_error
    -> replan: switching strategy
 4  healthy    observe  -
completed: recovered and tests pass
```

The example uses the default conservative engine and only opts into `replan`
for the `stalled` state, so it never stops the run on its own.
