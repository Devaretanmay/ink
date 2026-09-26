# CLI

Four commands. All of them read a trajectory file and stream it through the
in-process runtime.

## inspect

Summarise a trajectory. Detection only, so what you see is what a default
observe-only runtime would report.

```bash
microloop inspect run.jsonl
```

```
Microloop trajectory analysis (schema 0.3.0)
Steps          10
Status         stalled
Detected at    step 6
Reasons        repeated_action_result, repeated_error, state_stagnation
Evidence       Same action, observation and supplied state recurred
Action         observe (default policy: observe only)
```

`Status` and `Reasons` describe the final step. `Detected at` is the first step
whose state was not `healthy`.

## replay

Re-run *recorded events* through the current engine, one decision per step.

```bash
microloop replay run.jsonl
microloop replay run.jsonl --json
```

`replay` does not reproduce the original agent execution. No model is called and
no tools run. A trajectory recorded by an older engine may classify differently
today, which is why `replay` is useful for checking engine changes.

Unlike `inspect`, `replay` also shows the recommendation a host policy would
receive, since it is showing per-step output rather than a summary.

## monitor

Stream a trajectory as it is written.

```bash
microloop monitor run.jsonl
microloop monitor run.jsonl --follow --interval 0.5
```

```
Microloop
trajectory run.jsonl (following)

   3  STALLED    repeated_action_result repeated_error
      Same action, observation and supplied state recurred
      -> REPLAN (recommended)

completed
Steps             10
Stalls            3
Warnings          0
Regressions       0
Recommended       3
Recovered         yes
```

`--follow` keeps reading as an agent appends steps. `--no-color` disables ANSI
colour. Recommendations are labelled, because a `monitor` view is advisory: it
never acts on them.

## doctor

Check that the native extension loads, and report the runtime environment.

```bash
microloop doctor
```

```
[ok] microloop  0.3.0
[ok] schema     0.3.0
[ok] runtime    native (PyO3)
[ok] python     3.13.12
[ok] platform   Darwin arm64
[ok] native extension importable
```

Exits non-zero if the extension cannot be imported.

## Trajectory format

One JSON object per line. `schema_version` is optional; a missing one is assumed
current. A present one must have a matching major version:

| Field | |
|---|---|
| `schema_version` | `"0.3.0"` |
| `step` | monotonic integer |
| `action` | string, non-empty |
| `observation` | string, may be empty |
| `state` | object of string values, optional |
| `metrics` | object of numbers, optional |
| `metadata` | object of string values, optional |

A non-numeric or mismatched major version is a hard error naming the file and
line. Unknown fields are ignored rather than rejected.
