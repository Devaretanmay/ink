"""A small task set for real-model runs.

The generated tasks in :mod:`integrations.coding_harness.tasks` are one-line
arithmetic with an obviously wrong operator. A competent model solves one of those
in a single turn, so both arms converge and Microloop never fires. That is a
legitimate outcome and it is represented here as the control, but it cannot tell a
well-calibrated controller apart from one that does nothing.

These are real Python bugs with a hidden verifier. The failure message is the
shape a real test runner produces, so the model gets what a developer would get:
the expected value, not the cause. Only the first failing case is reported, which
is what ``pytest`` does and what makes the harder task take more than one
attempt.

``dedupe-unhashable`` is the one worth spending a call budget on. Its obvious fix
is wrong, so the model has to work out *why* the first attempt failed rather than
pattern-match the traceback. A task that can only be passed in one attempt cannot
show whether an intervention helped.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from integrations.coding_harness.harness import Task

__all__ = ["Case", "build_real_tasks"]

#: One hidden test: a function name, its arguments, and what it must return.
Case = tuple[str, tuple[Any, ...], Any]

_VERSION_SOURCE = (
    "def parse_version(text):\n"
    '    """Parse a version string into a tuple of integers."""\n'
    '    parts = text.lstrip("v").split(".")\n'
    "    return tuple(int(p) for p in parts)\n"
)

_DEDUPE_SOURCE = (
    "def dedupe(items):\n"
    '    """Remove duplicates, preserving the order of first appearance."""\n'
    "    seen = set()\n"
    "    out = []\n"
    "    for item in items:\n"
    "        if item not in seen:\n"
    "            seen.add(item)\n"
    "            out.append(item)\n"
    "    return out\n"
)


def _run(source: str, case: Case) -> tuple[bool, str]:
    """Run one hidden case against the file the agent wrote."""
    name, args, expected = case
    namespace: dict[str, Any] = {}
    try:
        exec(compile(source, "solution.py", "exec"), namespace)  # noqa: S102
        actual = namespace[name](*args)
    except Exception as error:  # noqa: BLE001 - report it the way pytest would
        return False, f"1 failed: {type(error).__name__}: {error}"
    if actual != expected:
        return False, f"1 failed: AssertionError: {name} returned {actual!r}, want {expected!r}"
    return True, "1 passed"


def _verifier(cases: tuple[Case, ...]) -> Callable[[Path], tuple[bool, str]]:
    """Return a verifier that runs the cases in order and reports the first failure."""

    def verify(workspace: Path) -> tuple[bool, str]:
        source = (workspace / "solution.py").read_text(encoding="utf-8")
        for case in cases:
            passed, message = _run(source, case)
            if not passed:
                return False, message
        return True, f"{len(cases)} passed"

    return verify


def build_real_tasks() -> list[Task]:
    """Return the real-model task set, easiest first.

    ``version-padding`` is the control. The failure names the expected value, so
    a competent model fixes it in one attempt and both arms converge. A run where
    Microloop correctly does nothing is a result, not a wasted call.

    ``dedupe-unhashable`` is the task that can distinguish arms. The first
    obvious fix -- coerce to a tuple, or sort before inserting -- either still
    crashes or drops items, so the model has to read why the first attempt failed
    instead of pattern-matching it.
    """
    version = Task(
        name="version-padding",
        prompt=(
            "solution.py defines parse_version(text). It has a bug.\n"
            "Make parse_version always return a 3-part tuple so versions compare "
            "correctly, padding missing parts with zero. run_tests is the only "
            "measure of success."
        ),
        filename="solution.py",
        wrong_source=_VERSION_SOURCE,
        correct_source=(
            "def parse_version(text):\n"
            '    """Parse a version string into a tuple of integers."""\n'
            '    parts = text.lstrip("v").split(".")\n'
            "    numbers = [int(p) for p in parts]\n"
            "    while len(numbers) < 3:\n"
            "        numbers.append(0)\n"
            "    return tuple(numbers)\n"
        ),
        verify=_verifier(
            (
                ("parse_version", ("v1.2.3",), (1, 2, 3)),
                ("parse_version", ("1.2",), (1, 2, 0)),
            )
        ),
    )
    dedupe = Task(
        name="dedupe-unhashable",
        prompt=(
            "solution.py defines dedupe(items). It has a bug.\n"
            "Make dedupe return items with duplicates removed, in order of first "
            "appearance, for any items including lists and dicts, which cannot be "
            "put in a set. run_tests is the only measure of success."
        ),
        filename="solution.py",
        wrong_source=_DEDUPE_SOURCE,
        correct_source=(
            "def dedupe(items):\n"
            '    """Remove duplicates, preserving the order of first appearance."""\n'
            "    out = []\n"
            "    for candidate in items:\n"
            "        if not any(candidate == kept for kept in out):\n"
            "            out.append(candidate)\n"
            "    return out\n"
        ),
        verify=_verifier(
            (
                ("dedupe", ([1, 2, 1, 3],), [1, 2, 3]),
                ("dedupe", ([[1], [1], [2]],), [[1], [2]]),
            )
        ),
    )
    return [version, dedupe]
