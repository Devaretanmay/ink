# Analyze a trajectory from the command line

Four commands. All of them read a trajectory file and stream it through the
in-process runtime.

## inspect

Tell the trajectory as a story. Default output is for a person: only meaningful
transitions, in plain language, then one outcome line.

```bash
microloop inspect run.jsonl
```

```
Microloop


  step 4   stall detected
           repeated action 3 times
           same error repeated 3 times

  step 5   progress resumed
           verification improved

Trajectory ended progressing after 5 steps.
```

An event is a transition, not a state. Entering a problem reports it, getting
worse reports it, and the first healthy step afterwards reports that progress
resumed. A problem that simply persists stays one event rather than one per step.
A run with nothing to report says so in one line.

`inspect` runs the observe-only policy and never prints a recommendation. It is
answering what happened, not what a host would have done about it.

The wording is presentation only. `healthy` reads as `progressing`, `warning` as
`uncertain`, and the reason enums are never shown. The API keeps the enum names.

### Layers

```bash
microloop inspect run.jsonl            # the story, for a person
microloop inspect run.jsonl --verbose  # every step, plus the advised intervention
microloop inspect run.jsonl --json     # exact enums, evidence structures, severity
```

`--json` returns the full representation, including `final_state`, `worst_state`,
per-step `evidence` with the step numbers behind it, `severity` and the policy.

Note that `inspect` never says a run "recovered", because in observe-only mode
Microloop did not cause the recovery. It says progress resumed, which is only an
observation.

## replay

The same trajectory as a timeline. `inspect` tells you what happened, `replay`
shows you how.

```bash
microloop replay run.jsonl
```

```
  1  progressing
  2  progressing

  3  stalled
     repeated action 3 times
     same error repeated 3 times
     -> replan
  4  progressing
     verification improved
```

`replay` uses the recommendation policy, so it shows what a host would be told,
marked with `->`. It re-runs recorded events through the current engine; it does
not reproduce the original agent execution. `--json` emits one decision per line
as JSONL.

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
