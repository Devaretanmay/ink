# Analyze a trajectory from the command line

Five commands. All of them read a trajectory file and stream it through the
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

With `--verbose`, each step also lists the candidates the scored controller
considered, best first, so you can see why it chose what it chose:

```
  6  stalled
     repeated action 3 times
     same error repeated 3 times
     candidates
       replan            +0.60
       continue          +0.08
       stop              +0.05
     -> replan
```

Candidate scores only appear in the verbose and JSON views; normal output stays
one line per step.

## explain

Explain the runtime recommendation, using progress and runtime state together.

```bash
microloop explain run.jsonl
```

```
Microloop explain

  step 3   stalled
           since step 3
           repeated action 3 times
           same error repeated 3 times
           runtime
           model     sonnet
           context   72%
           cost      $0.84
           recommendation replan (trajectory stalled)

Trajectory ended stalled after 3 steps.
```

`explain` is the runtime view: it shows the recommendation and the conditions it
was made under, and adds a runtime block when the host recorded one. It uses the
scored controller, like `replay`, so an intervening step also prints its
`candidates` block. `--json` prints the exact representation, including
`progress`, `runtime` and the `recommendation` with its `trace` for every step.

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

## stats

Summarize the local episode store: what was attempted, in what situation, whether
the run finished, and the cost per successful task.

```bash
microloop stats
microloop stats .microloop/experiments.db
microloop stats --json
```

```
Microloop runtime statistics

Episodes                160
Successful              77

Adaptations             646

replan
  attempted             646
  improved              624

By arm
  arm                   tasks   succeeded   cost / success
  eager                 40      18          $0.00
  patient               40      22          $0.00
  static                40      21          $0.00
  wary                  40      16          $0.00

Where each action was chosen

stalled / repeated_action_result, repeated_error, state_stagnation / replan
  attempted             479
  improved              457 (95%)

Runs that finished, by action applied
  (an action usually fires on the runs that were already in
   trouble, so read this beside the arm table, not instead of it)
  no adaptation (control)       70%
  replan (646 runs)             12%

Cost / successful task  $0.00
```

Three readings, and they disagree on purpose:

- **By arm** is the controlled comparison. Each row ran the same tasks with the
  same agent, so it is the only table here that can show a policy effect.
- **Where each action was chosen** segments by situation, so `stalled +
  repeated_error` can be compared against `stalled` on its own.
- **Runs that finished** reports outcome rather than progress, beside a control
  group. Read the warning printed with it: an action usually fires on runs that
  were already in trouble, so its success rate is confounded downward.

`improved` means progress recovered after the action, not that the action caused
success. In the run above, 95% of replans were scored `improved` while the best
policy was barely ahead of doing nothing. Treat it as a detector-health signal,
not as evidence an adaptation helped.

The store is a local SQLite file written by `EpisodeStore` users and by the
experiment runner. There is no server and no telemetry; if the
file does not exist yet, `stats` says so rather than creating one.

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
| `runtime` | object, optional: `model`, `context_tokens`, `context_limit`, `input_tokens`, `output_tokens`, `cost`, `elapsed_seconds`, `tool_calls`, `remaining_budget` |

A non-numeric or mismatched major version is a hard error naming the file and
line. Unknown fields are ignored rather than rejected. The `runtime` field is
optional, so a trajectory recorded before it existed still reads; `inspect`,
`replay` and `explain` add a runtime block when it is present.
