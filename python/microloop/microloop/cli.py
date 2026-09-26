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


def _inspect(args: argparse.Namespace) -> int:
    schema, records = _read_trajectory(args.trajectory)
    # Detection only: `inspect` reports what the detectors found, so it runs the
    # default observation-only policy and never shows a host's replan/stop choice.
    monitor = _observing()
    first_detected: int | None = None
    last = None
    for index, record in enumerate(records, start=1):
        decision = monitor.observe_event(_event_from_record(record, index))
        if decision.status != ProgressState.Healthy and first_detected is None:
            first_detected = decision.step
        last = decision
    assert last is not None

    reasons = ", ".join(last.reasons) if last.reasons else "none"
    evidence = last.evidence[0]["detail"] if last.evidence else "none"
    print(f"Microloop trajectory analysis (schema {schema})")
    print(f"{'Steps':<15}{len(records)}")
    print(f"{'Status':<15}{last.status}")
    print(f"{'Detected at':<15}{'step ' + str(first_detected) if first_detected else 'none'}")
    print(f"{'Reasons':<15}{reasons}")
    print(f"{'Evidence':<15}{evidence}")
    print(f"{'Action':<15}{last.intervention} (default policy: observe only)")
    return 0


def _replay(args: argparse.Namespace) -> int:
    schema, records = _read_trajectory(args.trajectory)
    monitor = _recommending()
    for index, record in enumerate(records, start=1):
        decision = monitor.observe_event(_event_from_record(record, index))
        payload = {
            "schema_version": schema,
            "step": decision.step,
            "status": decision.status,
            "reasons": decision.reasons,
            "intervention": decision.intervention,
            "severity": decision.severity,
            "verified_progress": decision.verified_progress,
        }
        if args.json:
            print(json.dumps(payload))
        else:
            reasons = ",".join(decision.reasons) or "-"
            print(
                f"{decision.step:>4}  {decision.status:<10} "
                f"{decision.intervention:<8} {reasons}"
            )
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
        help="summarize a trajectory JSONL (detection only, no intervention policy)",
    )
    inspect_parser.add_argument("trajectory")
    replay_parser = sub.add_parser(
        "replay",
        help="re-run recorded events through the current engine",
    )
    replay_parser.add_argument("trajectory")
    replay_parser.add_argument("--json", action="store_true", help="emit decisions as JSONL")
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
