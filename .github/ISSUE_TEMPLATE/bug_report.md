---
name: Bug report
about: Something in Ink behaves incorrectly
title: '[BUG] '
labels: 'bug'
assignees: ''
---

**What happened**

What you observed, and what you expected instead.

**Reproduction**

The smallest case that shows it. A decision-site reproduction is often enough:

```python
from ink import DecisionSite, Ink
```

or, for the CLI, the command and its full output.

**Environment**

- Ink version: `python -c "import ink; print(ink.__version__)"`
- Python version
- OS:

**Anything else**

Relevant config, or the `DecisionSite(...)` arguments you passed.
