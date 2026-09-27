"""Microloop CLI: inspect, replay, monitor and doctor.

Reads schema 0.3.0 trajectory JSONL and streams it through the native runtime.
``monitor`` can follow a live file as an agent appends steps.

``replay`` re-runs *recorded events* through the current Microloop engine. It
does not reproduce the original agent execution: no model is called and no tools
run. A trajectory recorded by an older engine may therefore classify differently
today.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from typing import Any

from . import (
    SCHEMA_VERSION,
    Decision,
    Event,
    InterventionAction,
    Monitor,
    Policy,
    ProgressState,
    __version__,
)

# Trajectory schema major version this runtime can read. A different major
# version changes the meaning of fields, so it is rejected rather than guessed.
_SCHEMA_MAJOR = int(SCHEMA_VERSION.split(".", 1)[0])

# Recommendation policy for `replay` and `monitor`: surfaces what a host *could*
# do without ever taking it. `inspect` deliberately does not use this; see below.
_RECOMMENDATION_POLICY = Policy(
    warning=InterventionAction.Observe,
    stalled=InterventionAction.Replan,
    regressing=InterventionAction.Replan,
    cooldown_steps=1,
    max_interventions=10_000,
)

_COLORS = {
    "healthy": "\033[32m",
    "warning": "\033[33m",
    "stalled": "\033[31m",
    "regressing": "\033[35m",
}
_RESET = "\033[0m"


def _paint(text: str, status: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{_COLORS.get(status, '')}{text}{_RESET}"


def _check_schema(record: dict[str, Any], path: str, lineno: int) -> None:
    """Reject a trajectory this runtime cannot read."""
    raw = record.get("schema_version")
    if raw is None:
        return
    version = str(raw)
    major = version.partition(".")[0]
    if not major.isdigit():
        raise SystemExit(
            f"{path}:{lineno}: unreadable schema_version {version!r}; "
            f"expected '<major>.<minor>' (this runtime reads {SCHEMA_VERSION})"
        )
    if int(major) != _SCHEMA_MAJOR:
        raise SystemExit(
            f"{path}:{lineno}: incompatible trajectory schema {version}; "
            f"this runtime reads schema {SCHEMA_VERSION}. Trajectory major versions "
            "must match. Upgrade Microloop, or re-record the trajectory with "
            "this schema version."
        )


def _load_record(path: str, lineno: int, line: str) -> dict[str, Any]:
    try:
        record = json.loads(line)
    except json.JSONDecodeError as error:
        raise SystemExit(f"{path}:{lineno}: invalid JSON: {error}") from error
    if not isinstance(record, dict):
        raise SystemExit(f"{path}:{lineno}: expected a JSON object")
    _check_schema(record, path, lineno)
    return record


def _iter_records(path: str, follow: bool = False, interval: float = 0.25):
    """Yield trajectory records, optionally following a file as it grows."""
    with open(path, encoding="utf-8") as handle:
        lineno = 0
        while True:
            where = handle.tell()
            raw = handle.readline()
            if raw == "":
                if not follow:
                    return
                handle.seek(where)
                time.sleep(interval)
                continue
            line = raw.strip()
            if not line:
                continue
            lineno += 1
            yield _load_record(path, lineno, line)


def _read_trajectory(path: str) -> tuple[str, list[dict[str, Any]]]:
    schema = SCHEMA_VERSION
    records: list[dict[str, Any]] = []
    for record in _iter_records(path):
        if "schema_version" in record:
            schema = str(record["schema_version"])
        records.append(record)
    if not records:
        raise SystemExit(f"{path}: no trajectory events found")
    return schema, records


def _event_from_record(record: dict[str, Any], fallback_step: int) -> Event:
    return Event(
        step=int(record.get("step", fallback_step)),
        action=str(record.get("action", "")),
        observation=str(record.get("observation", "")),
        state=record.get("state"),
        metrics=record.get("metrics"),
        metadata=record.get("metadata"),
    )


def _recommending() -> Monitor:
    return Monitor(policy=_RECOMMENDATION_POLICY)


def _observing() -> Monitor:
    """Default runtime posture: detection without any intervention."""
    return Monitor()


def commands() -> dict[str, Any]:
    return {
        "inspect": _inspect,
        "replay": _replay,
        "monitor": _monitor_live,
        "doctor": _doctor,
    }


#: Progress states ordered by how much attention they warrant, least first.
_STATE_RANK = {"healthy": 0, "warning": 1, "stalled": 2, "regressing": 3}

#: How the internal states read to a person. The API keeps the enum names;
#: this is presentation only. `healthy` is never shown, because in a trajectory
#: "progressing" says what actually happened.
_STATE_WORD = {
    "healthy": "progressing",
    "warning": "uncertain",
    "stalled": "stalled",
    "regressing": "regressing",
}

#: Headline for a step that is not progressing. Phrased whole, so it does not
#: read as "pattern detected detected".
_ISSUE_PHRASE = {
    "warning": "pattern detected",
    "stalled": "stall detected",
    "regressing": "regression detected",
}

#: One line per reason, in plain language. The enum names are for --json.
_REASON_PHRASE = {
    "repeated_action_result": "repeated action",
    "normalized_repetition": "repeated action, ignoring volatile values",
    "repeated_error": "same error repeated",
    "state_stagnation": "test failures unchanged",
    "state_oscillation": "state oscillating",
    "regression": "verifier got worse",
}

#: Reasons where a count reads naturally. Oscillation and regression do not
#: take one: their numbers are in the evidence detail instead.
_COUNTABLE = {
    "repeated_action_result",
    "normalized_repetition",
    "repeated_error",
    "state_stagnation",
}


def _describe(decision: Decision) -> list[str]:
    """Render one decision's evidence as plain-language lines."""
    lines: list[str] = []
    for item in decision.evidence:
        phrase = _REASON_PHRASE.get(item["reason"], item["reason"])
        steps = item.get("steps") or []
        if item["reason"] in _COUNTABLE and len(steps) > 1:
            lines.append(f"{phrase} {len(steps)} times")
        elif item["reason"] == "regression" and item.get("detail"):
            lines.append(f"{phrase}: {item['detail'].split(': ', 1)[-1]}")
        else:
            lines.append(phrase)
    if decision.verified_progress:
        lines.append("verification improved")
    return lines


def _interesting(decision: Decision) -> bool:
    """True when a step is worth a human seeing: a problem, or a recovery."""
    return bool(decision.evidence) or decision.verified_progress


def _steps(count: int) -> str:
    return "1 step" if count == 1 else f"{count} steps"


def _outcome(decision: Decision, total: int, with_count: bool) -> str:
    ended = _STATE_WORD.get(decision.status, decision.status)
    if not with_count:
        return f"Trajectory ended {ended}."
    return f"Trajectory ended {ended} after {_steps(total)}."


def _report(records: list[dict[str, Any]], monitor: Monitor, verbose: bool) -> None:
    """Print the timeline of meaningful steps, then the outcome.

    Only transitions are shown by default: a step that is progressing and adds
    nothing is skipped. That is the whole point of the command, and it is why
    this is not a rendering of the final Decision.
    """
    print("Microloop")
    print()
    decisions: list[Decision] = []
    for index, record in enumerate(records, start=1):
        decisions.append(monitor.observe_event(_event_from_record(record, index)))

    # An event is a *transition*, not a state. Entering a problem reports it,
    # getting worse reports it, and the first healthy step afterwards reports
    # that progress resumed. A problem that simply persists stays one event
    # rather than one per step.
    shown = 0
    previous: str | None = None
    for decision in decisions:
        issue = decision.status != ProgressState.Healthy
        entered = issue and decision.status != previous
        resumed = (
            not issue and previous is not None and previous != ProgressState.Healthy
        )
        headline = (
            _ISSUE_PHRASE.get(decision.status, decision.status)
            if entered
            else "progress resumed" if resumed else None
        )
        if headline is not None:
            shown += 1
            print()
        if headline is not None or verbose:
            label = headline or _STATE_WORD.get(decision.status, decision.status)
            print(f"  step {decision.step}   {label}")
            for line in _describe(decision):
                print(f"           {line}")
        previous = decision.status

    final = decisions[-1]
    if shown == 0:
        print(f"No progress issues across {_steps(len(decisions))}.")
        print()
        print(_outcome(final, len(decisions), with_count=False))
    else:
        print()
        print(_outcome(final, len(decisions), with_count=True))


def _inspect(args: argparse.Namespace) -> int:
    schema, records = _read_trajectory(args.trajectory)
    if args.json:
        print(json.dumps(_machine_report(schema, records), indent=2))
        return 0
    # Detection only. A human reading a trajectory wants to know what happened,
    # not what a host policy would have done about it, so the default render
    # carries no policy line at all.
    _report(records, _observing(), args.verbose)
    if args.verbose:
        _print_verbose(records)
    return 0


def _print_verbose(records: list[dict[str, Any]]) -> None:
    print()
    print("Every step, and what a host policy would be advised:")
    # One monitor for the whole run. A fresh Monitor per step would reset the
    # history window and make every step look healthy.
    monitor = _recommending()
    for index, record in enumerate(records, start=1):
        decision = monitor.observe_event(_event_from_record(record, index))
        print(
            f"  step {decision.step}  status={decision.status}  "
            f"intervention={decision.intervention}  severity={decision.severity:.1f}"
        )
        for item in decision.evidence:
            print(f"           {item['reason']}: {item['detail']} (steps {item['steps']})")
    print()
    print("Policy is observe-only for this report; no action was taken.")


def _machine_report(schema: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Exact representation: enums, evidence structures and steps, unchanged."""
    decisions: list[dict[str, Any]] = []
    # One monitor for the whole run, for the same reason as _print_verbose.
    monitor = _recommending()
    for index, record in enumerate(records, start=1):
        decisions.append(monitor.observe_event(_event_from_record(record, index)).__dict__)
    states = [d["status"] for d in decisions]
    worst = max(states, key=lambda s: _STATE_RANK.get(s, 0)) if states else "healthy"
    return {
        "schema_version": schema,
        "steps": len(records),
        "final_state": states[-1] if states else "healthy",
        "worst_state": worst,
        "policy": "observe-only",
        "interventions": sum(
            1 for d in decisions if d["intervention"] != InterventionAction.Observe
        ),
        "decisions": decisions,
    }


def _replay(args: argparse.Namespace) -> int:
    """The timeline. `inspect` says what happened, this shows how."""
    schema, records = _read_trajectory(args.trajectory)
    monitor = _recommending()
    if args.json:
        for index, record in enumerate(records, start=1):
            decision = monitor.observe_event(_event_from_record(record, index))
            print(
                json.dumps(
                    {
                        "schema_version": schema,
                        "step": decision.step,
                        "status": decision.status,
                        "reasons": decision.reasons,
                        "evidence": decision.evidence,
                        "intervention": decision.intervention,
                        "severity": decision.severity,
                        "verified_progress": decision.verified_progress,
                    }
                )
            )
        return 0

    print()
    for index, record in enumerate(records, start=1):
        decision = monitor.observe_event(_event_from_record(record, index))
        lines = _describe(decision)
        intervening = decision.intervention != InterventionAction.Observe
        if lines or intervening or args.verbose:
            print()
        print(f"  {decision.step}  {_STATE_WORD.get(decision.status, decision.status)}")
        for line in lines:
            print(f"     {line}")
        if intervening:
            print(f"     -> {decision.intervention}")
        if args.verbose and not lines and decision.status == ProgressState.Healthy:
            print("     no evidence")
    return 0


def _monitor_live(args: argparse.Namespace) -> int:
    color = sys.stdout.isatty() and not args.no_color
    monitor = _recommending()
    steps = 0
    stalls = 0
    warnings = 0
    regressions = 0
    interventions = 0
    recovered = False
    seen_issue = False

    print("Microloop")
    print(f"trajectory {args.trajectory}" + (" (following)" if args.follow else ""))
    print()
    try:
        for record in _iter_records(args.trajectory, follow=args.follow, interval=args.interval):
            steps += 1
            decision = monitor.observe_event(_event_from_record(record, steps))
            if decision.status == "stalled":
                stalls += 1
            elif decision.status == "warning":
                warnings += 1
            elif decision.status == "regressing":
                regressions += 1
            if decision.status != "healthy":
                seen_issue = True
            elif seen_issue:
                recovered = True

            header = _paint(f"{decision.status.upper():<10}", decision.status, color)
            reasons = " ".join(decision.reasons)
            print(f"{decision.step:>4}  {header} {reasons}".rstrip())
            for item in decision.evidence:
                print(f"      {item['detail']}")
            if decision.intervention != InterventionAction.Observe:
                interventions += 1
                print(f"      -> {decision.intervention.upper()} (recommended)")
            print()
    except KeyboardInterrupt:
        print()

    print("completed" if not seen_issue or recovered else "stopped")
    print(f"{'Steps':<18}{steps}")
    print(f"{'Stalls':<18}{stalls}")
    print(f"{'Warnings':<18}{warnings}")
    print(f"{'Regressions':<18}{regressions}")
    print(f"{'Recommended':<18}{interventions}")
    print(f"{'Recovered':<18}{'yes' if recovered else 'no'}")
    return 0


def _doctor(_args: argparse.Namespace) -> int:
    checks = [
        ("microloop", __version__),
        ("schema", SCHEMA_VERSION),
        ("runtime", "native (PyO3)"),
        ("python", platform.python_version()),
        ("platform", f"{platform.system()} {platform.machine()}"),
    ]
    ok = True
    for name, value in checks:
        print(f"[ok] {name:<10} {value}")
    try:
        from .microloop_core import Monitor as _Native  # noqa: F401

        print("[ok] native extension importable")
    except Exception as error:  # pragma: no cover - environment dependent
        ok = False
        print(f"[fail] native extension: {error}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="microloop", description="Microloop runtime tools")
    parser.add_argument(
        "--version",
        action="version",
        version=f"microloop {__version__} (schema {SCHEMA_VERSION}, runtime: native)",
    )
    sub = parser.add_subparsers(dest="command")
    inspect_parser = sub.add_parser(
        "inspect",
        help="tell a trajectory as a story: what happened, in plain language",
    )
    inspect_parser.add_argument("trajectory")
    inspect_parser.add_argument(
        "--verbose",
        action="store_true",
        help="also print every step and the advised intervention",
    )
    inspect_parser.add_argument(
        "--json", action="store_true", help="print the exact representation"
    )
    replay_parser = sub.add_parser(
        "replay",
        help="re-run recorded events through the current engine",
    )
    replay_parser.add_argument("trajectory")
    replay_parser.add_argument("--json", action="store_true", help="emit decisions as JSONL")
    replay_parser.add_argument(
        "--verbose",
        action="store_true",
        help="mark steps that produced no evidence",
    )
    monitor_parser = sub.add_parser("monitor", help="print a live progress view")
    monitor_parser.add_argument("trajectory")
    monitor_parser.add_argument(
        "-f", "--follow", action="store_true", help="keep reading as the file grows"
    )
    monitor_parser.add_argument(
        "--interval", type=float, default=0.25, help="poll interval in seconds when following"
    )
    monitor_parser.add_argument("--no-color", action="store_true", help="disable ANSI color")
    sub.add_parser("doctor", help="check the native runtime")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    return commands()[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
