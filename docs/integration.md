# Integrating Microloop

Use `DecisionSite(name, state_schema, choices, fallback_revision="...")` and a
persistent `Microloop(path=...)` client. Field types are `string`, `integer`,
`number`, and `boolean`; append `?` for missing/nullable fields. Extra fields,
nonfinite numbers, and booleans passed as integers are rejected before execution.
Change `fallback_revision` whenever the model/prompt semantics change.

Call `client.decide(site=site, state=state, fallback=callback, task_id=...)`.
`callback()` returns a choice string or `FallbackResult(choice, model_calls=...,
input_tokens=..., output_tokens=..., cost=..., provider=..., model=...)`.
Use `decide_async` with an awaited callback. Its cancellation and exceptions propagate.
Routing runs in a worker thread under `decide_async`; engine `predict` itself stays
synchronous, so event-loop-heavy hosts should still isolate the client.

Named-site shorthand may infer a primitive schema on first use. Register an
explicit contract for nullable states and reliable storage-outage fallback.
Callbacks never execute business actions. Only the caller executes the returned
choice. Every result carries its ID, source, version, fallback reason, confidence,
and whether history was recorded.

## Learning and verification

1. `client.compile(site, engine="exact")` fits from outcome-bearing fallback history.
2. `client.calibrate(site, verifier=verify, requirements=requirements)` freezes
   empirically supported coverage. Supply `PromotionRequirements` explicitly;
   there are no universal production defaults.
3. Run fresh tasks through the site in shadow and report their actual outcomes.
4. `client.evaluate(site, verifier=verify)` evaluates untouched holdout and fresh
   shadow evidence, then promotes atomically if every check passes.
5. Schedule `client.maintenance()` after reporting outcomes to re-evaluate active
   paths. The evaluation window and maintenance cadence determine detection delay.

A verifier takes `(state, choice)` and returns `Outcome(quality, verifier,
verifier_version, evidence)`. It must replay actions independently in an isolated
environment, not call the original model or assume agreement means success.
Microloop trusts the application's verifier; unsuitable verifiers cannot establish
real outcome quality. Side-effectful production actions must never be replayed.

For Laya, construct `LayaEngine(checkpoint=local_path)` from the private engine
module and pass it in `Microloop(engines=[...])`; select `engine="laya"` at compile.
This backend is optional and private while its interface matures.

`compile(..., replace_existing=True)` retires the old path and starts over.
Requalification after demotion requires fresh shadow outcomes; it cannot reactivate
from pre-demotion observations alone.

## Local data

Explicit export contains state and outcomes; choose its destination deliberately.
Retention excludes sites with artifacts so promotion evidence remains auditable.
Keep sensitive fields out of the decision contract unless they are required.
Provider credentials are owned by the application and never belong in state.
