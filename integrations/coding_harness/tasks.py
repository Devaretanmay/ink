"""A controlled task set for the static-vs-adaptive experiment.

These are small, fully deterministic coding tasks: one function, one hidden
verifier, a known-wrong implementation and a known fix. They are deliberately
synthetic -- the point of Pass 4 is to measure the *controller*, not to publish a
benchmark result. The same harness runs real tasks later by swapping the task
set; nothing else changes.

The task set carries a stated prior over how an agent behaves when it gets stuck.
Those proportions were fixed before any run, and they are what stop the
comparison from being a foregone conclusion. Four kinds of agent appear, and
between them they cover every way an adaptive runtime can be right or wrong:

``recovers``
    Sees the failing assertion and fixes it unaided. The static arm wins these
    too, and any adaptation Microloop performs here is cost for nothing.
``stubborn``
    Understands the task but will not budge without being pushed. These are the
    tasks an adaptive runtime exists for.
``plausible``
    Genuinely changes approach on instruction and still lands on a wrong answer.
    Replan will look like it worked, and the outcome row is the point.
``hopeless``
    Never moves off its first strategy. No adaptation helps, so this measures
    how much the adaptive arm wastes trying.

A harness whose static arm cannot succeed cannot lose. That is a demonstration,
not an experiment, and it is the specific mistake this task set is built to
avoid.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from integrations.coding_harness.harness import Task
from integrations.coding_harness.providers import AgentBehaviour, Ceiling

__all__ = ["BEHAVIOUR_MIX", "build_tasks", "task_count"]

_OPERATORS: dict[str, Callable[[int, int], int]] = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
}

#: The wrong operator the starting implementation uses. Each is a plausible bug.
_WRONG_OPERATOR = {"+": "-", "-": "+", "*": "+"}

#: Plausible-but-wrong rewrites an agent might land on instead of the fix.
_DISTRACTORS: dict[str, tuple[str, ...]] = {
    "+": ("return {b} + {a}", "return {a} + {b} + 1", "return {a} * {b}"),
    "-": ("return {b} - {a}", "return {a} - {b} - 1", "return {a} + {b}"),
    "*": ("return {a} + {b}", "return {a} * {b} * 2", "return {b} * {a}"),
}

#: The stated prior, as ``(kind, per-block share, base patience, settle)``.
#: ``settle`` is how much evidence the agent wants on an approach before it will
#: act on an instruction rather than treat it as an interruption. Stubborn agents
#: settle slowly, which is exactly why interrupting them is expensive. Fixed
#: before any run; changing it invalidates a comparison against a recorded
#: result.
#:
#: ``close`` is the population that makes the comparison mean anything. The other
#: kinds are either easy or lost outright, and an intervention cannot change an
#: outcome that was never in doubt. A close agent finishes with only a few steps
#: to spare, so the steps an unnecessary adaptation wastes decide the run -- and
#: they can decide it either way. Without it, a runtime that intervenes on every
#: stalled step scores exactly the same as one that never intervenes, which is
#: not a finding about interventions so much as a gap in the task set.
BEHAVIOUR_MIX: tuple[tuple[str, int, int, int], ...] = (
    ("recovers", 8, 1, 1),
    ("stubborn", 4, 8, 3),
    ("close", 3, 4, 3),
    ("plausible", 3, 3, 2),
    ("hopeless", 2, 3, 1),
)

_BLOCK = sum(share for _, share, _, _ in BEHAVIOUR_MIX)


def _call(source: str) -> Any:
    """Evaluate a candidate ``solve()`` and return its result, or the error."""
    namespace: dict[str, Any] = {}
    try:
        exec(compile(source, "task.py", "exec"), namespace)  # noqa: S102
        return namespace["solve"]()
    except Exception as error:  # noqa: BLE001 - a bad candidate is simply not a fix
        return error


def _wrong_only(candidates: tuple[str, ...], expected: int) -> tuple[str, ...]:
    """Keep only candidates that are genuinely wrong.

    A distractor that happens to compute the right answer would silently turn a
    hard task into an easy one, so the task set is checked rather than trusted.
    """
    kept: list[str] = []
    for template in candidates:
        source = "def solve():\n    " + template + "\n"
        result = _call(source)
        if not isinstance(result, int) or result == expected:
            continue
        if source not in kept:
            kept.append(source)
    return tuple(kept)


def _arith_verify(expected: int) -> Callable[[Path], tuple[bool, str]]:
    def verify(workspace: Path) -> tuple[bool, str]:
        source = (workspace / "task.py").read_text(encoding="utf-8")
        actual = _call(source)
        if isinstance(actual, Exception):
            return False, f"1 failed: {type(actual).__name__}: {actual}"
        if actual != expected:
            return (
                False,
                f"1 failed: AssertionError: expected solve() == {expected}, got {actual!r}",
            )
        return True, "1 passed"

    return verify


def _behaviour_for(index: int, distractors: tuple[str, ...]) -> AgentBehaviour:
    """Pick this task's agent behaviour from the stated mix."""
    position = index % _BLOCK
    consumed = 0
    for kind, share, base, settle in BEHAVIOUR_MIX:
        consumed += share
        if position < consumed:
            ceiling = {
                "recovers": Ceiling.Correct,
                "stubborn": Ceiling.Correct,
                "close": Ceiling.Correct,
                "plausible": Ceiling.Plausible,
                "hopeless": Ceiling.Hopeless,
            }[kind]
            return AgentBehaviour(
                # Vary patience within a kind so the set is not perfectly uniform,
                # without ever moving a task across the kind it was assigned.
                patience=base + (index // _BLOCK) % 3,
                ceiling=ceiling,
                distractors=distractors,
                settle=settle,
            )
    raise AssertionError("behaviour mix does not cover the block")  # pragma: no cover


def build_tasks(count: int = 32) -> list[Task]:
    """Return ``count`` deterministic tasks."""
    tasks: list[Task] = []
    operators = list(_OPERATORS)
    for index in range(count):
        operator = operators[index % len(operators)]
        wrong = _WRONG_OPERATOR[operator]
        a = index + 2
        b = index % 5 + 1
        expected = _OPERATORS[operator](a, b)
        wrong_source = f"def solve():\n    return {a} {wrong} {b}\n"
        correct_source = f"def solve():\n    return {a} {operator} {b}\n"
        distractors = _wrong_only(
            tuple(template.format(a=a, b=b) for template in _DISTRACTORS[operator]), expected
        )
        tasks.append(
            Task(
                name=f"arith-{index:02d}",
                prompt=(
                    "task.py defines solve(). It is wrong. "
                    "Make solve() return the value the tests expect."
                ),
                filename="task.py",
                wrong_source=wrong_source,
                correct_source=correct_source,
                verify=_arith_verify(expected),
                behaviour=_behaviour_for(index, distractors),
            )
        )
    return tasks


def task_count() -> int:
    return len(build_tasks())
