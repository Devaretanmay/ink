# Microloop: trajectory failure detection and recovery

Status: approved direction; Experiment 001 implementation in progress.

## Problem and user

Engineers running autonomous coding agents cannot distinguish productive iteration
from a run that is repeating failures. A live process and valid tool calls do not
prove progress. The first user is a developer who already owns an agent harness,
can emit tool results, and can measure task completion independently.

## Product promise to validate

Microloop is a local failure detection engine for autonomous agents. It observes
an execution trajectory and returns evidence of non-progress. The host chooses
whether to observe, request a replan, or stop. The company hypothesis is that
this increases completion at a comparable total resource budget.

This is a hypothesis, not an established product claim. The pasted strategy
notes motivate the direction; their external research citations and competitor
claims are not treated as verified evidence in this specification.

## Boundaries

The product owns Event → bounded Trajectory → Decision. A separate Policy maps
that decision to Intervention. The host owns tools, model calls, permissions,
verification and all side effects. Detection works offline, without prompts,
source code, embeddings or network calls. Callers can provide stable opaque
fingerprints and optional verifier metrics. No cloud ingestion is required.

We do not build an orchestrator, durable workflow engine, security gateway,
observability dashboard, universal correctness judge, or compression company.
Compression/CCR is retained outside the active workspace for possible later use.
Do not enable it in only one experimental condition: it would confound the test.

## Experiment 001 scope

1. Versioned events: run identity, strictly increasing step, action fingerprint,
   observation success and fingerprint, optional error fingerprint and verified
   state metrics. Missing information remains unknown, never a zero or success.
2. Deterministic local detectors: repeated action **and result**, recurrent error,
   verified stagnation. Add state oscillation and regression after this baseline.
   Repeated tool names, changing files, and successful shell exit alone do not
   establish failure or goal progress.
3. Decisions: HEALTHY, WARNING, STALLED, REGRESSING; evidence with source steps
   and explicit reasons. HEALTHY means no detected anomaly, not task completion.
   Scores are heuristic severity, not calibrated probabilities.
4. Conservative policy: OBSERVE by default; opt-in REPLAN with a cooldown and
   intervention cap; STOP only at a configured host budget. No automatic rollback,
   restart, model switching, or destructive side effects in the detector.
5. Rust monitor and Python binding; bounded history and run isolation; invalid
   or out-of-order events fail without altering monitor state.
6. Append-only event/decision/intervention records and final run records. Record
   actual usage; unsupported token or dollar accounting stays null.
7. A frozen development manifest and baseline-compatible benchmark tooling.
   Recorded-trajectory replay validates detectors only; it cannot measure recovery
   lift. Synthetic faults are separately labeled and never pooled with real tasks.

## Experimental protocol

Use one minimal coding harness, identical base prompt/tools/model/settings and
hard per-run budgets across four conditions: vanilla, one bounded retry/restart,
LLM supervisor, Microloop with recovery feedback. Supervisor usage and all retry
usage count toward the same total resource limits. Record intervention prompts.
Start with one model, then repeat with a second model at validation.

Freeze 30 SWE-bench Verified development tasks before any condition runs. Select
with a recorded seed from a pinned dataset revision, without treatment outcomes.
Do not invent easier/medium/harder labels: stratify only if a documented,
pre-treatment difficulty source is available. The 100-task validation set must
be disjoint and untouched during tuning. Three trials per task per condition
means 360 development runs **per model**. Freeze policy and thresholds before
validation. Terminal-Bench generalization and a ~50-case injected fault suite
follow the natural-failure baseline; neither substitutes for it.

Success comes only from the official task evaluator. Agent self-report, a clean
exit, patch existence and local test success are not resolved-task labels.
Include failed agent runs in the denominator. Keep infrastructure errors visible
and use a predeclared rerun policy; never silently drop difficult tasks.

## Measurement and decision gates

Primary: absolute completion-rate lift versus vanilla and versus retry, with
paired task/trial comparisons and task-cluster bootstrap confidence intervals.
Report sample counts, missing pairs and uncertainty. Do not pool models.

Secondary: tool/model calls, tokens, wall time, measured monitoring latency,
cost per resolved task, intervention count and steps, and wasted actions after
an independently annotated failure onset. Define the annotation protocol before
using wasted-action claims; total actions saved is a different metric.

Recovery rate: intervened runs that subsequently regain verified progress and
ultimately resolve / all intervened runs. A success after intervention alone is
not proof of causal recovery. False interventions need counterfactual evidence;
a paired control success with treatment failure is a damaging-intervention
proxy, not a definitive false-positive label. Keep unknown labels null.

Validation targets (not results): ≥100 real tasks, ≥3 trials per condition/model,
≥8 percentage points lift, <5% damaging-intervention proxy among intervened
paired runs, and ≥15% fewer independently labeled wasted actions. Report
confidence intervals, including for harms. A second model and later a second
harness must reproduce useful gains before broad generalization claims.

Strong improvement supports expanding the product. Modest improvement narrows
its failure-class scope. No reproducible lift means revise or abandon the
commercial thesis; do not compensate with new features or marketing claims.

## Acceptance criteria

- The default Rust build has no compression, storage, proxy or network dependency.
- Same-run ordered events yield deterministic decisions; fresh monitors do not
  share history; observation storage is bounded.
- A healthy repeated verification with improving metrics is not interrupted.
- Missing metrics cannot manufacture stagnation or regression.
- Error recurrence uses an error fingerprint, not just a broad exception class.
- Feedback cites only observed evidence; it invents neither root causes nor
  previous strategies. Repeated warnings cannot flood the host context.
- Python calls execute the same compiled Rust monitor, not a substitute detector.
- Replay and fault-test outputs explicitly state that task lift is unmeasured.
- Real evaluation configuration and artifacts permit another engineer to rerun
  the same comparisons. No success percentages before those runs exist.

## Delivery order

M0: prune conflicting product surfaces, write this PRD, update contributor docs.
M1: events, monitor, Python binding, recorder and replay; exercise a real local
     tool trajectory and deterministic failure/healthy cases.
M2: freeze task manifests; wire a minimal agent and official SWE-bench evaluator;
     run vanilla first, then retry, Microloop and supervisor under equal budgets.
M3: inspect failures, tune on development only, freeze and run held-out validation.
M4: second model/harness, richer detectors and recovery policies only if supported.

## Risks

Heuristics can mislabel legitimate retries, slow exploration and coarse verifier
plateaus. State metrics need stable scope identifiers (the same test suite and
revision of its definition) and fresh observations. Fingerprints preserve local
processing but still need careful treatment when exported. Results depend on
model version, stochasticity, evaluator infrastructure and trial counts. A local
monitor alone cannot prove goal drift or choose a correct recovery strategy.
