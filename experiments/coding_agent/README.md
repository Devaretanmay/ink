# Ink coding-agent experiment

This harness runs a real OpenCode task in an isolated detached Git worktree, evaluates the
result with a repository-owned command, and only then applies Ink instrumentation to the recorded
tool events. Keeping classification post-run guarantees that the first experiment cannot change
the agent's prompt, tool results, patch, or control flow.

## Paired run

Use the same model and manifest for both arms. Keep separate output directories and use a fresh
observe database. The included smoke task replays a real Ink cleanup task from its original base
commit.

```bash
PYTHONPATH=python/ink:. python3 -m experiments.coding_agent.run \
  --manifest experiments/coding_agent/tasks/ink-cleanup-smoke.json \
  --mode control --model PROVIDER/MODEL \
  --output-dir /tmp/ink-control --ink-db /tmp/ink-control/decisions.db

PYTHONPATH=python/ink:. python3 -m experiments.coding_agent.run \
  --manifest experiments/coding_agent/tasks/ink-cleanup-smoke.json \
  --mode observe --model PROVIDER/MODEL \
  --output-dir /tmp/ink-observe --ink-db /tmp/ink-observe/decisions.db

PYTHONPATH=python/ink:. python3 -m experiments.coding_agent.report \
  /tmp/ink-control/telemetry.jsonl /tmp/ink-observe/telemetry.jsonl
```

## Modes

- `control`: no Ink client is created and no decision rows are emitted.
- `observe`: decisions and independent outcomes are recorded with the fast path disabled.
- `shadow`: an existing SHADOW artifact may predict, but the deterministic fallback remains
  authoritative. The harness rejects a database containing an ACTIVE artifact.
- `active`: Ink's normal runtime is used. Only artifacts promoted to ACTIVE by Ink may serve.

The harness never compiles, calibrates, evaluates, promotes, or changes qualification settings.
Those lifecycle operations remain explicit and use Ink's existing APIs and mathematics.

## Task manifest

Every task pins a repository, commit, prompt, executable test command, accepted exit codes, and
timeout. Each arm receives a new detached worktree at that exact commit. The worktree is force
removed after the patch and verifier evidence are captured, including after failures.
