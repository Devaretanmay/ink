"""A close-call task band for real-model runs.

The first real-model run was null: `gpt-oss-120b` solved both tasks in three or
four steps, Microloop had nothing to detect, and zero adaptations fired. That is
the correct behaviour on an easy task and it is useless as an experiment, because
an adaptive runtime can only be measured where its intervention could have gone
either way.

The fix is the task distribution, not the controller. This module provides tasks
from six families that model how real regressions arrive, each with the property
that matters:

    the obvious first fix does not finish the task

That is enforced mechanically rather than asserted in prose. Every task carries a
``naive`` variant -- what a competent engineer writes after reading the first
failing assertion -- and :func:`validate` checks that the naive variant passes the
first hidden case and *still fails* a later one. A task whose obvious fix already
passes is a task that tests pattern-matching rather than debugging, and it is
rejected rather than shipped.

So each task is checked three ways:

    the buggy source fails                  (the task is real)
    the correct source passes               (the task is solvable)
    the naive fix passes case 1, fails case 2  (the task is a close call)

All three run offline and cost nothing.

**The band is not calibrated, and nothing here should pretend otherwise.** A
close-call property is necessary for the band to be useful; it is not evidence
that a given model lands in the 40-70% band. Only a real model can show that, and
it needs a call budget. :func:`summarise` reports a measured static success rate
against the band for exactly that reason.
"""
from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from integrations.coding_harness.harness import Task

__all__ = [
    "BAND",
    "FAMILIES",
    "TaskFiles",
    "TaskReport",
    "build_close_tasks",
    "close_call_report",
    "summarise",
    "validate",
]

#: A project: file path to source.
TaskFiles = dict[str, str]

#: The static success band worth measuring at. Below it both arms fail and the
#: comparison is about noise; above it the controller never fires.
BAND = (0.40, 0.70)


def _verifier(
    order: tuple[str, ...],
    cases: tuple[tuple[tuple[Any, ...], Any], ...],
) -> Callable[[Path], tuple[bool, str]]:
    """Report the first failing case only, the way pytest does.

    Showing the whole specification at once would let a model satisfy it by
    pattern rather than by reading, which is what this band exists to prevent.
    """

    def verify(workspace: Path) -> tuple[bool, str]:
        namespace: dict[str, Any] = {}
        try:
            for path in order:
                source = workspace / path
                if not source.exists():
                    return False, f"1 failed: FileNotFoundError: {path}"
                # Composed in dependency order rather than imported, so the
                # verifier is deterministic and holds no module state between
                # cases. The agent is still editing real files in a real
                # workspace; only the checking is simplified.
                exec(compile(source.read_text(encoding="utf-8"), path, "exec"), namespace)  # noqa: S102
        except Exception as error:  # noqa: BLE001 - the agent broke the project
            return False, f"1 failed: {type(error).__name__}: {error}"
        for args, expected in cases:
            try:
                actual = namespace["main"](*args)
            except Exception as error:  # noqa: BLE001
                return False, f"1 failed: {type(error).__name__}: {error}"
            if actual != expected:
                return (
                    False,
                    f"1 failed: AssertionError: main{args!r} returned {actual!r}, "
                    f"want {expected!r}",
                )
        return True, f"{len(cases)} passed"

    return verify


@dataclass
class _Spec:
    """One close-call task: three variants, hidden cases, and a family label."""

    name: str
    family: str
    prompt: str
    order: tuple[str, ...]
    buggy: TaskFiles
    naive: TaskFiles
    correct: TaskFiles
    cases: tuple[tuple[tuple[Any, ...], Any], ...]

    def build(self) -> Task:
        """Materialise as a runnable :class:`Task`.

        Only the buggy variant reaches the agent. ``naive`` and ``correct`` exist
        so the band can be checked, not so it can be leaked.
        """
        target = self.order[-1]
        return Task(
            name=self.name,
            prompt=self.prompt,
            filename=target,
            wrong_source=self.buggy[target],
            correct_source=self.correct[target],
            support_files={
                path: source for path, source in self.buggy.items() if path != target
            },
            verify=_verifier(self.order, self.cases),
        )


# --- multi-file regression ----------------------------------------------------
#
# Two independent faults. The first is what the reported assertion shows; the
# second is in a branch the failure never mentions. A fix for the first is not a
# fix for the task, which is the ordinary condition of real debugging.


def _regression(index: int) -> _Spec:
    helper_buggy = (
        "def with_charge(amount):\n"
        '    """Add the flat handling charge to an amount."""\n'
        "    return amount + 5\n"
    )
    helper_correct = (
        "def with_charge(amount):\n"
        '    """Add the flat handling charge, which does not apply to a refund."""\n'
        "    return amount + 5 if amount >= 0 else amount\n"
    )
    main_buggy = (
        "def main(items, delivery):\n"
        '    """Sum the items, adding the handling charge only when delivery is needed."""\n'
        "    if delivery:\n"
        "        return sum(with_charge(i) for i in items)\n"
        "    return sum(with_charge(i) for i in items)\n"
    )
    naive = (
        "def main(items, delivery):\n"
        '    """Sum the items, adding the handling charge only when delivery is needed."""\n'
        "    if delivery:\n"
        "        return sum(with_charge(i) for i in items)\n"
        "    return sum(i for i in items)\n"
    )
    correct = naive
    amount = 20 + index
    return _Spec(
        name=f"regression-{index:02d}",
        family="multi-file regression",
        prompt=(
            "Two files. helper.py has with_charge(amount); main.py has "
            "main(items, delivery). The handling charge is a flat 5 per order, it "
            "applies only when delivery is needed, and it never applies to a "
            "refund. main is wrong, and there may be more than one thing wrong. "
            "run_tests is the only measure of success."
        ),
        order=("helper.py", "main.py"),
        buggy={"helper.py": helper_buggy, "main.py": main_buggy},
        naive={"helper.py": helper_buggy, "main.py": naive},
        correct={"helper.py": helper_correct, "main.py": correct},
        cases=(
            # First: no delivery, so no charge at all.
            (([amount, amount + 1], False), amount * 2 + 1),
            # Second: delivery with a refund, so the charge applies to one item
            # and not the other. Fixing only the first case still charges it.
            (([amount, -5], True), amount + 5 - 5),
        ),
    )


# --- edge case in a refactor --------------------------------------------------
#
# Deduplication by hash, so it crashes on anything unhashable. The natural repair
# is a string key, and a string key has opinions about case.


def _edge(index: int) -> _Spec:
    source = (
        "def unique(items):\n"
        '    """Remove duplicates, preserving the order of first appearance."""\n'
        "    seen = set()\n"
        "    out = []\n"
        "    for item in items:\n"
        "        if item not in seen:\n"
        "            seen.add(item)\n"
        "            out.append(item)\n"
        "    return out\n"
        "\n"
        "\n"
        "def main(items):\n"
        "    return unique(items)\n"
    )
    naive = (
        "def unique(items):\n"
        '    """Remove duplicates, preserving the order of first appearance."""\n'
        "    seen = set()\n"
        "    out = []\n"
        "    for item in items:\n"
        "        key = str(item).lower()\n"
        "        if key not in seen:\n"
        "            seen.add(key)\n"
        "            out.append(item)\n"
        "    return out\n"
        "\n"
        "\n"
        "def main(items):\n"
        "    return unique(items)\n"
    )
    number = index + 1
    return _Spec(
        name=f"edge-{index:02d}",
        family="edge-case refactor",
        prompt=(
            "main.py has unique(items), used by main(items). Removing duplicates "
            "must work for any items, including lists and dicts, and two items are "
            "duplicates only when they are equal. main is wrong, and there may be "
            "more than one thing wrong. run_tests is the only measure of success."
        ),
        order=("main.py",),
        buggy={"main.py": source},
        naive={"main.py": naive},
        correct={
            "main.py": (
                "def unique(items):\n"
                '    """Remove duplicates, preserving the order of first appearance."""\n'
                "    out = []\n"
                "    for candidate in items:\n"
                "        if not any(candidate == kept for kept in out):\n"
                "            out.append(candidate)\n"
                "    return out\n"
                "\n"
                "\n"
                "def main(items):\n"
                "    return unique(items)\n"
            )
        },
        cases=(
            # First: unhashable items, which is what the traceback shows.
            (([[number], [number]],), [[number]]),
            # Second: items that differ only in case, which a case-folding key
            # treats as the same item.
            ((["a", "A"],), ["a", "A"]),
        ),
    )


# --- stateful across modules --------------------------------------------------
#
# Two faults in two files. The first is a balance that never moves off its
# opening value, which is what the reported assertion shows. The second is a
# refund whose sign is flipped, and the first failure never mentions it.


def _stateful(index: int) -> _Spec:
    account_buggy = (
        "class Account:\n"
        '    """A running balance."""\n'
        "\n"
        "    def __init__(self, opening=0):\n"
        "        self.entries = [('open', opening)]\n"
        "\n"
        "    def balance(self):\n"
        "        return self.entries[0][1]\n"
    )
    account_correct = (
        "class Account:\n"
        '    """A running balance."""\n'
        "\n"
        "    def __init__(self, opening=0):\n"
        "        self.entries = [('open', opening)]\n"
        "\n"
        "    def balance(self):\n"
        "        return sum(amount for _, amount in self.entries)\n"
    )
    ledger_buggy = (
        "def main(opening, events):\n"
        '    """Apply every event in order and return the running balance."""\n'
        "    account = Account(opening)\n"
        "    for kind, amount in events:\n"
        "        if kind == 'refund':\n"
        "            account.entries.append(('move', abs(amount)))\n"
        "        else:\n"
        "            account.entries.append(('move', amount))\n"
        "    return account.balance()\n"
    )
    ledger_correct = (
        "def main(opening, events):\n"
        '    """Apply every event in order and return the running balance."""\n'
        "    account = Account(opening)\n"
        "    for kind, amount in events:\n"
        "        if kind == 'refund':\n"
        "            account.entries.append(('move', -amount))\n"
        "        else:\n"
        "            account.entries.append(('move', amount))\n"
        "    return account.balance()\n"
    )
    opening = 100 + index
    return _Spec(
        name=f"stateful-{index:02d}",
        family="stateful across modules",
        prompt=(
            "Two files. account.py has an Account class; ledger.py has "
            "main(opening, events), where each event is a (kind, amount) pair and a "
            "refund is given as a positive magnitude that must reduce the balance. "
            "main must apply every event in order and return the running balance. "
            "It is wrong, and there may be more than one thing wrong. run_tests is "
            "the only measure of success."
        ),
        order=("account.py", "ledger.py"),
        buggy={"account.py": account_buggy, "ledger.py": ledger_buggy},
        naive={"account.py": account_correct, "ledger.py": ledger_buggy},
        correct={"account.py": account_correct, "ledger.py": ledger_correct},
        cases=(
            # First: a deposit, so the balance must have moved at all.
            ((opening, [("deposit", 7)]), opening + 7),
            # Second: a refund, which the first case never exercises.
            ((opening, [("deposit", 7), ("refund", 20)]), opening - 13),
        ),
    )


# --- API behaviour mismatch ---------------------------------------------------
#
# The call site compensates for a client that already matches its own docstring,
# and the client does nothing at all when asked for zero retries. Correcting the
# call site is a reasonable guess that leaves the second fault in place.


def _api(index: int) -> _Spec:
    client_buggy = (
        "def fetch(url, retries=1):\n"
        '    """Return a report of the response.\n'
        "\n"
        "    retries is the number of attempts, not the number of extra attempts.\n"
        "    A request is always made at least once, so retries=0 still tries once.\n"
        '    """\n'
        "    report = None\n"
        "    for _ in range(retries):\n"
        "        report = 'attempts=%d' % retries\n"
        "    return report\n"
    )
    client_correct = (
        "def fetch(url, retries=1):\n"
        '    """Return a report of the response.\n'
        "\n"
        "    retries is the number of attempts, not the number of extra attempts.\n"
        "    A request is always made at least once, so retries=0 still tries once.\n"
        '    """\n'
        "    tries = max(retries, 1)\n"
        "    report = None\n"
        "    for _ in range(tries):\n"
        "        report = 'attempts=%d' % tries\n"
        "    return report\n"
    )
    caller_buggy = (
        "def main(url, retries):\n"
        '    """Fetch a url, retrying as client.py documents."""\n'
        "    return fetch(url, retries - 1)\n"
    )
    caller_correct = (
        "def main(url, retries):\n"
        '    """Fetch a url, retrying as client.py documents."""\n'
        "    return fetch(url, retries)\n"
    )
    return _Spec(
        name=f"api-{index:02d}",
        family="api behaviour mismatch",
        prompt=(
            "Two files. client.py has fetch(url, retries), whose docstring says what "
            "retries means; caller.py has main(url, retries). main must retry as "
            "client.py documents. It is wrong, and there may be more than one thing "
            "wrong. run_tests is the only measure of success."
        ),
        order=("client.py", "caller.py"),
        buggy={"client.py": client_buggy, "caller.py": caller_buggy},
        naive={"client.py": client_buggy, "caller.py": caller_correct},
        correct={"client.py": client_correct, "caller.py": caller_correct},
        cases=(
            # First: the attempt count, which is what the caller gets wrong.
            (("u", 2), "attempts=2"),
            # Second: a single attempt. A client that honours retries=0
            # literally makes no request at all.
            (("u", 0), "attempts=1"),
        ),
    )


# --- API migration ------------------------------------------------------------
#
# Ported from a library whose flatten recursed, and the port also dropped the
# tuple handling the new API needs.


def _migration(index: int) -> _Spec:
    source = (
        "def flatten(nested):\n"
        '    """Flatten nested sequences, leaving strings and scalars intact."""\n'
        "    out = []\n"
        "    for item in nested:\n"
        "        if isinstance(item, list):\n"
        "            out.extend(item)\n"
        "        else:\n"
        "            out.append(item)\n"
        "    return out\n"
        "\n"
        "\n"
        "def main(nested):\n"
        "    return flatten(nested)\n"
    )
    naive = (
        "def flatten(nested):\n"
        '    """Flatten nested sequences, leaving strings and scalars intact."""\n'
        "    out = []\n"
        "    for item in nested:\n"
        "        if isinstance(item, list):\n"
        "            out.extend(flatten(item))\n"
        "        else:\n"
        "            out.append(item)\n"
        "    return out\n"
        "\n"
        "\n"
        "def main(nested):\n"
        "    return flatten(nested)\n"
    )
    correct = (
        "def flatten(nested):\n"
        '    """Flatten nested sequences, leaving strings and scalars intact."""\n'
        "    out = []\n"
        "    for item in nested:\n"
        "        if isinstance(item, (list, tuple)) and not isinstance(item, str):\n"
        "            out.extend(flatten(item))\n"
        "        else:\n"
        "            out.append(item)\n"
        "    return out\n"
        "\n"
        "\n"
        "def main(nested):\n"
        "    return flatten(nested)\n"
    )
    number = index + 1
    return _Spec(
        name=f"migration-{index:02d}",
        family="api migration",
        prompt=(
            "shapes.py has flatten(nested), ported from a library whose flatten "
            "handled arbitrary nesting and tuples. The port handles neither "
            "correctly. Strings and other scalars must be left intact. Fix it. "
            "run_tests is the only measure of success."
        ),
        order=("shapes.py",),
        buggy={"shapes.py": source},
        naive={"shapes.py": naive},
        correct={"shapes.py": correct},
        cases=(
            # First: nested lists, which is what the reported failure shows.
            (([1, [2, [3, 4]]],), [1, 2, 3, 4]),
            # Second: a tuple, which the recursive-but-list-only fix leaves whole.
            (([number, (number + 1, (number + 2,))],), [number, number + 1, number + 2]),
        ),
    )


# --- partial test suite -------------------------------------------------------
#
# Truncation where the contract says round half up. The two agree on most inputs,
# so the obvious fix clears the reported case and fails the next one -- because
# Python's round() breaks ties to even, which is not the same rule.


def _suite(index: int) -> _Spec:
    source = (
        "def main(subtotal, discount):\n"
        '    """Total after a percentage discount, in whole cents, rounded half up."""\n'
        "    return int(subtotal - subtotal * discount / 100)\n"
    )
    naive = (
        "def main(subtotal, discount):\n"
        '    """Total after a percentage discount, in whole cents, rounded half up."""\n'
        "    return round(subtotal - subtotal * discount / 100)\n"
    )
    correct = (
        "def main(subtotal, discount):\n"
        '    """Total after a percentage discount, in whole cents, rounded half up."""\n'
        "    # Half-up, done in integers so no float decides a cent.\n"
        "    return (subtotal * (100 - discount) * 2 + 100) // 200\n"
    )
    # Two subtotals, both landing on a half cent, chosen so the tie breaks the
    # same way as the contract in the first case and the other way in the second.
    # At a 50% discount the halved subtotal is x.5; Python's round() sends a tie
    # to the even neighbour, so the first subtotal is 3 mod 4 (where the even
    # neighbour is up) and the second is 1 mod 4 (where it is down).
    first = 203 + 4 * index
    second = first + 2
    return _Spec(
        name=f"suite-{index:02d}",
        family="partial test suite",
        prompt=(
            "billing.py has main(subtotal, discount). Totals are in whole cents and "
            "a half cent rounds up. main is wrong. Fix it. run_tests is the only "
            "measure of success."
        ),
        order=("billing.py",),
        buggy={"billing.py": source},
        naive={"billing.py": naive},
        correct={"billing.py": correct},
        cases=(
            # First: a half cent that rounds up, and where Python's round()
            # happens to agree with the contract.
            ((first, 50), (first * 50 * 2 + 100) // 200),
            # Second: a half cent that also rounds up, but whose tie Python's
            # round() resolves the other way.
            ((second, 50), (second * 50 * 2 + 100) // 200),
        ),
    )


#: The families, in emission order.
FAMILIES: tuple[tuple[str, Callable[[int], _Spec]], ...] = (
    ("regression", _regression),
    ("edge", _edge),
    ("stateful", _stateful),
    ("api", _api),
    ("migration", _migration),
    ("suite", _suite),
)

_VARIANTS = 4


def _specs(count: int) -> list[_Spec]:
    """``count`` specs: every family, four variants each, cycling.

    Variants within a family differ in their data, not their bug, so each block
    is four tasks of the same kind at different difficulty. Emitting family by
    family rather than round-robin means truncating the count gives whole blocks
    instead of a band missing a fault type entirely.
    """
    specs: list[_Spec] = []
    block = 0
    while len(specs) < count:
        for _, factory in FAMILIES:
            for variant in range(_VARIANTS):
                if len(specs) >= count:
                    break
                specs.append(factory(block * _VARIANTS + variant))
            if len(specs) >= count:
                break
        block += 1
    return specs


def build_close_tasks(count: int = 24) -> list[Task]:
    """Return ``count`` close-call tasks."""
    return [spec.build() for spec in _specs(count)]


# --- validation ---------------------------------------------------------------


@dataclass
class TaskReport:
    """The three checks every task in the band must pass."""

    name: str
    family: str
    buggy_fails: bool
    correct_passes: bool
    naive_passes_first: bool
    naive_passes_all: bool
    buggy_message: str = ""
    naive_message: str = ""

    @property
    def is_close_call(self) -> bool:
        """A real task, a solvable task, and a first fix that does not finish it."""
        return (
            self.buggy_fails
            and self.correct_passes
            and self.naive_passes_first
            and not self.naive_passes_all
        )

    def problem(self) -> str | None:
        if not self.buggy_fails:
            return f"passes before any change ({self.buggy_message})"
        if not self.correct_passes:
            return "the correct source does not pass, so the task is unsolvable"
        if not self.naive_passes_first:
            return "the obvious fix does not even pass the reported case"
        if self.naive_passes_all:
            return "the obvious fix already finishes the task, so it is not close"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "family": self.family,
            "close_call": self.is_close_call,
            "buggy_message": self.buggy_message,
            "naive_message": self.naive_message,
        }


def _apply(workspace: Path, files: TaskFiles) -> None:
    for path, source in files.items():
        target = workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")


def validate(tasks: list[Task] | None = None) -> list[TaskReport]:
    """Check the band mechanically. Runs offline and costs nothing.

    Every task must fail before it is touched, pass once fixed, and have a naive
    fix that clears the reported case without finishing the job. A task that
    fails any of these is not in the band, and the report says which.
    """
    wanted = {task.name for task in tasks} if tasks is not None else None
    reports: list[TaskReport] = []
    for spec in _specs(len(FAMILIES) * _VARIANTS):
        if wanted is not None and spec.name not in wanted:
            continue
        task = spec.build()
        workspace = Path(tempfile.mkdtemp())
        task.prepare(workspace)
        buggy_passed, buggy_message = task.verify(workspace)
        _apply(workspace, spec.correct)
        correct_passed, _ = task.verify(workspace)
        _apply(workspace, spec.naive)
        naive_passed, naive_message = task.verify(workspace)
        # "Passes the reported case" means the naive fix gets past the first
        # hidden case. Run the cases one at a time to find out.
        first_only = _verifier(spec.order, spec.cases[:1])
        naive_first_passed, _ = first_only(workspace)
        reports.append(
            TaskReport(
                name=spec.name,
                family=spec.family,
                buggy_fails=not buggy_passed,
                correct_passes=correct_passed,
                naive_passes_first=naive_first_passed,
                naive_passes_all=naive_passed,
                buggy_message=buggy_message,
                naive_message=naive_message,
            )
        )
    return reports


def close_call_report() -> str:
    """Render the validation, and say plainly that it is not calibration."""
    reports = validate()
    lines = ["Close-call band validation", ""]
    for report in reports:
        mark = "ok  " if report.is_close_call else "FAIL"
        lines.append(f"[{mark}] {report.name:<16} {report.family}")
        if not report.is_close_call:
            lines.append(f"         {report.problem()}")
    close = sum(1 for report in reports if report.is_close_call)
    lines.append("")
    lines.append(f"{close}/{len(reports)} tasks are genuine close calls")
    lines.append("")
    lines.append(
        "This checks that the obvious first fix does not finish the task. It is "
        "NOT calibration: only a real model can say whether it lands in the "
        f"{BAND[0]:.0%}-{BAND[1]:.0%} band, and that needs a call budget. Run the "
        "static arm over the band and read summarise()."
    )
    return "\n".join(lines)


def summarise(successes: int, total: int) -> str:
    """Report a measured static success rate against the band worth measuring at.

    The band is the whole experiment. Below it both arms fail and the comparison
    is about noise; above it the controller never fires; at either extreme a
    result means nothing, and saying so is more useful than a number.
    """
    if not total:
        return "no runs recorded; the band cannot be assessed"
    rate = successes / total
    low, high = BAND
    where = f"the {low:.0%}-{high:.0%} band"
    if low <= rate <= high:
        return (
            f"static success {rate:.0%} ({successes}/{total}) is inside {where}: "
            f"the task set can tell a helpful intervention from a harmful one."
        )
    if rate > high:
        return (
            f"static success {rate:.0%} ({successes}/{total}) is above {where}: the "
            f"model is not getting stuck, so Microloop will not fire and this "
            f"measures nothing. Make the tasks harder."
        )
    return (
        f"static success {rate:.0%} ({successes}/{total}) is below {where}: both "
        f"arms fail and the comparison is about noise. Make the tasks easier."
    )
