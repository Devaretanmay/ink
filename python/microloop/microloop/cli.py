"""Microloop CLI: inspect, replay, monitor, explain, stats and doctor.

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
from pathlib import Path
from typing import Any

from . import (
    SCHEMA_VERSION,
    Decision,
    Event,
    InterventionAction,
    Monitor,
    Policy,
    ProgressState,
    ScoredController,
    __version__,
)

# Trajectory schema major version this runtime can read. A different major
# version changes the meaning of fields, so it is rejected rather than guessed.
_SCHEMA_MAJOR = int(SCHEMA_VERSION.split(".", 1)[0])

# Recommendation policy for `replay` and `monitor`: surfaces what a host *could*
# do without ever taking it. `inspect` deliberately does not use this; see below.
# The scored strategy is used so `--verbose` and `explain` can show candidates.
_RECOMMENDATION_POLICY = Policy(
    warning=InterventionAction.Observe,
    stalled=InterventionAction.Replan,
    regressing=InterventionAction.Replan,
    cooldown_steps=1,
    max_interventions=10_000,
)
_RECOMMENDATION_CONTROLLER = ScoredController(
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
        runtime=record.get("runtime"),
    )


def _recommending() -> Monitor:
    return Monitor(controller=_RECOMMENDATION_CONTROLLER)


def _trace_lines(decision: Decision) -> list[str]:
    """Render the scored controller's candidates, best first."""
    recommendation = decision.recommendation
    trace = recommendation.trace if recommendation is not None else None
    if trace is None or not trace.candidates:
        return []
    ranked = sorted(trace.candidates, key=lambda candidate: candidate.score, reverse=True)
    rows = [f"  {candidate.action:<18}{candidate.score:+.2f}" for candidate in ranked]
    return ["candidates", *rows]


def _observing() -> Monitor:
    """Default runtime posture: detection without any intervention."""
    return Monitor()


def commands() -> dict[str, Any]:
    return {
        "inspect": _inspect,
        "replay": _replay,
        "monitor": _monitor_live,
        "explain": _explain,
        "stats": _stats,
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


def _runtime_lines(decision: Decision) -> list[str]:
    """Render the runtime conditions behind a step, when the host reported any."""
    runtime = decision.runtime
    if runtime is None:
        return []
    lines: list[str] = []

    def add(label: str, value: Any) -> None:
        lines.append(f"{label:<10}{value}")

    if runtime.model:
        add("model", runtime.model)
    if runtime.context_utilization is not None:
        add("context", f"{runtime.context_utilization * 100:.0f}%")
    if runtime.cost is not None:
        add("cost", f"${runtime.cost:.2f}")
    if runtime.remaining_budget is not None:
        add("budget", f"${runtime.remaining_budget:.2f} left")
    if runtime.elapsed_seconds is not None:
        add("elapsed", f"{runtime.elapsed_seconds:.0f}s")
    if runtime.tool_calls is not None:
        add("tool calls", runtime.tool_calls)
    if not lines:
        return []
    return ["runtime", *lines]


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
            for line in _runtime_lines(decision):
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
        for line in _trace_lines(decision):
            print(f"           {line}")
    print()
    print("Policy is observe-only for this report; no action was taken.")


def _machine_report(schema: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Exact representation: enums, evidence structures and steps, unchanged."""
    decisions: list[dict[str, Any]] = []
    # One monitor for the whole run, for the same reason as _print_verbose.
    monitor = _recommending()
    for index, record in enumerate(records, start=1):
        decisions.append(monitor.observe_event(_event_from_record(record, index)).to_dict())
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
            payload = {
                "schema_version": schema,
                "step": decision.step,
                "status": decision.status,
                "reasons": decision.reasons,
                "evidence": decision.evidence,
                "intervention": decision.intervention,
                "severity": decision.severity,
                "verified_progress": decision.verified_progress,
            }
            if decision.progress is not None:
                payload["progress"] = decision.progress.to_dict()
            if decision.runtime is not None:
                payload["runtime"] = decision.runtime.to_dict()
            if decision.recommendation is not None:
                payload["recommendation"] = decision.recommendation.to_dict()
            print(json.dumps(payload))
        return 0

    print()
    for index, record in enumerate(records, start=1):
        decision = monitor.observe_event(_event_from_record(record, index))
        lines = _describe(decision)
        runtime_lines = _runtime_lines(decision)
        intervening = decision.intervention != InterventionAction.Observe
        if lines or runtime_lines or intervening or args.verbose:
            print()
        print(f"  {decision.step}  {_STATE_WORD.get(decision.status, decision.status)}")
        for line in lines:
            print(f"     {line}")
        for line in runtime_lines:
            print(f"     {line}")
        if args.verbose:
            for line in _trace_lines(decision):
                print(f"     {line}")
        if intervening:
            print(f"     -> {decision.intervention}")
        if args.verbose and not lines and decision.status == ProgressState.Healthy:
            print("     no evidence")
    return 0


def _explain(args: argparse.Namespace) -> int:
    """Explain why the runtime would act, using progress and runtime together."""
    schema, records = _read_trajectory(args.trajectory)
    if args.json:
        print(json.dumps(_machine_report(schema, records), indent=2))
        return 0
    monitor = _recommending()
    decisions = [
        monitor.observe_event(_event_from_record(record, index))
        for index, record in enumerate(records, start=1)
    ]
    print("Microloop explain")
    print()
    previous: str | None = None
    shown = 0
    for decision in decisions:
        recommendation = decision.recommendation
        intervening = recommendation is not None and recommendation.should_intervene
        transition = decision.status != previous
        previous = decision.status
        if not transition and not intervening:
            continue
        shown += 1
        print(f"  step {decision.step}   {_STATE_WORD.get(decision.status, decision.status)}")
        if decision.progress is not None and decision.progress.since_step:
            print(f"           since step {decision.progress.since_step}")
        for line in _describe(decision):
            print(f"           {line}")
        for line in _runtime_lines(decision):
            print(f"           {line}")
        for line in _trace_lines(decision):
            print(f"           {line}")
        if recommendation is not None:
            reason = recommendation.reason.replace("_", " ")
            print(f"           recommendation {recommendation.action} ({reason})")
        print()
    final = decisions[-1]
    if shown == 0:
        print(f"No progress issues across {_steps(len(decisions))}.")
        print()
    print(_outcome(final, len(decisions), with_count=shown > 0))
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


#: Action names read better in a report than the raw enum values.
_ACTION_LABEL = {
    "replan": "replan",
    "escalate_model": "model escalation",
    "deescalate_model": "model de-escalation",
    "compact_context": "context compaction",
}


def _stats(args: argparse.Namespace) -> int:
    """Aggregate the local episode store: what worked, and what it cost."""
    from .store import DEFAULT_DB_PATH, EpisodeStore

    path = args.database or DEFAULT_DB_PATH
    if path != ":memory:" and not Path(path).exists():
        if args.json:
            print(json.dumps({"episodes": 0, "by_action": {}, "arms": {}}))
        else:
            print("Microloop runtime statistics")
            print()
            print(f"No episodes recorded in {path}.")
        return 0

    store = EpisodeStore(path)
    try:
        summary = store.stats()
    finally:
        store.close()

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print("Microloop runtime statistics")
    print()
    print(f"{'Episodes':<24}{summary['episodes']}")
    print(f"{'Successful':<24}{summary['successful']}")
    print()
    print(f"{'Adaptations':<24}{summary['adaptations']}")
    for action, bucket in sorted(summary["by_action"].items()):
        print()
        print(_ACTION_LABEL.get(action, action))
        print(f"  {'attempted':<22}{bucket['attempted']}")
        for outcome in ("improved", "no_change", "regressed"):
            if bucket.get(outcome):
                print(f"  {outcome:<22}{bucket[outcome]}")

    arms = summary.get("arms") or {}
    if len(arms) > 1:
        print()
        print("By arm")
        print(f"  {'arm':<22}{'tasks':<8}{'succeeded':<12}{'cost / success'}")
        for name, bucket in sorted(arms.items()):
            cost = bucket.get("cost_per_success")
            price = "n/a" if cost is None else f"${cost:.2f}"
            print(
                f"  {name:<22}{bucket['episodes']:<8}{bucket['successful']:<12}{price}"
            )

    segments = summary.get("segments") or {}
    if segments:
        print()
        print("Where each action was chosen")
        for state, rows in segments.items():
            for row in rows:
                if row["attempted"] < 1:
                    continue
                signals = ", ".join(row["signals"]) or "no signals"
                rate = row["improved_rate"]
                share = "n/a" if rate is None else f"{rate * 100:.0f}%"
                label = _ACTION_LABEL.get(row["action"], row["action"])
                print()
                print(f"{_STATE_WORD.get(state, state)} / {signals} / {label}")
                print(f"  {'attempted':<22}{row['attempted']}")
                print(f"  {'improved':<22}{row['improved']} ({share})")

    attribution = summary.get("attribution") or {}
    by_action_success = attribution.get("by_action") or {}
    if by_action_success:
        control = (attribution.get("control_no_adaptation") or {}).get("success_rate")
        control_text = "n/a" if control is None else f"{control * 100:.0f}%"
        print()
        print("Runs that finished, by action applied")
        print("  (an action usually fires on the runs that were already in")
        print("   trouble, so read this beside the arm table, not instead of it)")
        print(f"  {'no adaptation (control)':<30}{control_text}")
        for action, bucket in by_action_success.items():
            rate = bucket["success_rate"]
            share = "n/a" if rate is None else f"{rate * 100:.0f}%"
            label = _ACTION_LABEL.get(action, action)
            runs = bucket["episodes"]
            print(f"  {f'{label} ({runs} runs)':<30}{share}")

    print()
    cost = summary["cost_per_success"]
    print(f"{'Cost / successful task':<24}{'n/a' if cost is None else f'${cost:.2f}'}")
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
    explain_parser = sub.add_parser(
        "explain",
        help="explain the runtime recommendation from progress and runtime state",
    )
    explain_parser.add_argument("trajectory")
    explain_parser.add_argument(
        "--json", action="store_true", help="print the exact representation"
    )
    stats_parser = sub.add_parser(
        "stats", help="summarize the local episode store"
    )
    stats_parser.add_argument(
        "database",
        nargs="?",
        default=None,
        help="path to the episode database (default .microloop/episodes.db)",
    )
    stats_parser.add_argument(
        "--json", action="store_true", help="print the aggregate as JSON"
    )
    sub.add_parser("doctor", help="check the native runtime")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--legacy" in argv:
        argv.remove("--legacy")
    elif argv and (argv[0] in {"sites", "compile", "evaluate", "maintenance", "export", "retain"}
                   or (argv[0] == "inspect" and len(argv) > 1
                       and ("--db" in argv or not any(
                           Path(a).is_file() for a in argv[1:] if not a.startswith("-"))))):
        from .decision_cli import main as decision_main
        return decision_main(argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    return commands()[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
