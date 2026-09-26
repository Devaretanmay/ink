# Coding agent example

An offline, **deterministic** demonstration of the recovery loop. It uses the
real public API (`Monitor`, `Policy`, `InterventionAction`) against a real
temporary repository, but the agent's failure and recovery path is *scripted*,
not the result of a model deciding what to do. It is a worked example of the
integration pattern, not a benchmark.

The agent starts with a wrong fix strategy. Because it repeats the same failed
edit and the same assertion, Microloop classifies the trajectory as `stalled` and
returns a `replan` intervention. The agent then switches strategy and the test
passes.

## Run it

From the repository root, with the workspace dev extra installed:

```bash
pip install -e '.[dev]'
maturin develop --manifest-path python/microloop/Cargo.toml
python examples/coding-agent/agent.py
```

This example is run in CI (`make check`), so the public API it exercises cannot
break without the build noticing.

Expected output, stable across runs:

```
 1  healthy    observe  -
 2  healthy    observe  -
 3  stalled    replan   repeated_action_result repeated_error
    -> replan: switching strategy
 4  healthy    observe  -
completed: recovered and tests pass
```

It exits `0` on recovery and `1` if the run collapses, so it is safe to assert on
in a test.

The example opts into `replan` for the `stalled` state only, and leaves
`regressing` and the budget defaults alone, so it never stops the run on its own.
