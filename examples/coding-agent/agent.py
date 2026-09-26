"""A small coding agent that uses Microloop to recover from a stall.

Runs fully offline. The "repository" is a temporary directory containing a
deliberately buggy function and a single test. Each agent step applies an edit
strategy and runs the test. Microloop watches the trajectory; when the agent
keeps repeating a failed strategy, it emits a ``replan`` intervention and the
agent switches approach.

Run with:

    python examples/coding-agent/agent.py
"""
from __future__ import annotations

import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from microloop import InterventionAction, Monitor, Policy

TARGET = "target.py"
CORRECT = "def add(a, b):\n    return a + b\n"


def apply_symmetric_fix(path: Path) -> None:
    """Strategy A (wrong for subtraction-based bug): keep the operator."""
    path.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")


def apply_operator_fix(path: Path) -> None:
    """Strategy B (correct): the operator is the bug."""
    path.write_text(CORRECT, encoding="utf-8")


def run_tests(path: Path) -> tuple[bool, str]:
    """Execute the single hidden test against the current source.

    The source is compiled in-memory rather than imported so that equal-sized
    rewrites in the same second cannot hit a stale ``__pycache__`` entry.
    """
    namespace: dict = {}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    try:
        assert namespace["add"](2, 3) == 5
    except AssertionError:
        return False, "AssertionError: expected add(2, 3) == 5"
    return True, "1 passed"


def build_monitor() -> Monitor:
    policy = Policy(
        stalled=InterventionAction.Replan,
        regressing=InterventionAction.Replan,
        cooldown_steps=1,
        max_interventions=3,
    )
    return Monitor(policy=policy)


def main() -> int:
    strategies: list[Callable[[Path], None]] = [apply_symmetric_fix, apply_operator_fix]
    strategy = 0

    with tempfile.TemporaryDirectory() as workdir:
        target = Path(workdir) / TARGET
        apply_symmetric_fix(target)

        monitor = build_monitor()
        for step in range(1, 13):
            strategies[strategy](target)
            passed, output = run_tests(target)

            metadata = {"error": output} if not passed else None
            if not passed:
                metadata = dict(metadata or {})
                metadata.update(
                    {
                        "verifier": "example-test",
                        "verification_id": f"run-{step}",
                    }
                )

            decision = monitor.observe(
                action=f"apply {strategies[strategy].__name__}",
                observation=output,
                metrics={"exit_code": 0.0 if passed else 1.0},
                metadata=metadata,
                step=step,
            )

            print(
                f"{step:>2}  {decision.status:<10} {decision.intervention:<8} "
                f"{' '.join(decision.reasons) or '-'}"
            )

            if decision.intervention == InterventionAction.Replan:
                strategy = min(strategy + 1, len(strategies) - 1)
                print("    -> replan: switching strategy")
            if decision.intervention == InterventionAction.Stop:
                print("    -> stop: run collapsed without recovery")
                return 1
            if passed:
                print("completed: recovered and tests pass")
                return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
