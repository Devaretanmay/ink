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

The smallest case that shows it. A decision-site reproduction is often enough:

```python
from microloop import DecisionSite, Microloop
```

or, for the CLI, the command and its full output.

**Environment**

- Microloop version: `python -c "import microloop; print(microloop.__version__)"`
- Python version
- OS:

**Anything else**

Relevant config, or the `DecisionSite(...)` arguments you passed.
