---
name: Bug report
about: Something in Microloop behaves incorrectly
title: '[BUG] '
labels: 'bug'
assignees: ''
---

**What happened**

What you observed, and what you expected instead.

**Reproduction**

The smallest case that shows it. A trajectory is often enough:

```jsonl
{"schema_version": "0.3.0", "step": 1, "action": "pytest tests/", "observation": "4 failed", "metrics": {"exit_code": 1, "failures": 4}, "metadata": {"verifier": "pytest", "verification_id": "run-1"}}
```

or, for the CLI, the command and its full output.

**Environment**

- Microloop version: `python -c "import microloop; print(microloop.__version__)"`
- Python version: `microloop doctor` reports it
- Binding: Python or Rust
- OS:

**Anything else**

Relevant config, or the `Monitor(...)` / `Policy(...)` arguments you passed.
