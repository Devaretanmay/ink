# Changelog

All notable changes to Microloop are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [0.3.0] (unreleased)

The productization release. Microloop is now a developer-ready local reliability
runtime rather than a benchmark harness.

### Added

- Frozen public API: `Event`, `Monitor`, `Decision`, `Policy`,
  `ProgressState`, `InterventionAction`.
- Canonical structured event model with `state`, `metrics` and `metadata`.
- Decoupled detection and policy: `Monitor.observe` classifies, `Policy`
  decides the intervention.
- Rust workspace with `crates/microloop-core` and `python/microloop`.
- CLI: `microloop inspect`, `microloop replay`, `microloop monitor` (live view,
  `--follow`) and `microloop doctor`.
- Schema `0.3.0` trajectory JSONL with real compatibility checking: a
  trajectory whose `schema_version` major version differs from the runtime's is
  rejected instead of being analyzed on a guess.
- Apache-2.0 licensing, with the license text shipped in the wheel. Previously
  dual MIT / Apache-2.0; MIT has been dropped.
- Benchmark provenance gate: every run bundle declares `run_mode`
  (`real`/`simulated`), simulated runs are confined to their own directory, and
  the report generator rejects anything that is not `real`.
- `pip install microloop` published from the release workflow, continuing the
  existing `microloop` distribution (0.1.1 .. 0.2.0).

### Changed

- Detection results are exposed as progress states; individual detectors are
  internal and surfaced only in `decision.reasons`.
- Automatic `replan`/`stop` is opt-in and rate-limited by cooldown and cap.
- The Rust detection internals (engine, detectors, history window,
  canonicalization) are private. Only `Monitor`, `Event`, `Decision`, `Policy`,
  the configs, the progress state and the intervention are public.
- `MonitorConfig` moved out of the event module into its own `config` module.
- `microloop inspect` now reports detection under the default observation-only
  policy, so it no longer displays a `replan` that a normal runtime would not
  produce. `replay` and `monitor` still show recommended interventions, labelled
  as recommendations.
- `severity` is documented as a fixed status-to-number lookup, explicitly not a
  probability or confidence.
- Trajectory schema compatibility is checked at the CLI boundary; this is
  documented rather than enforced inside the library, which stays
  version-agnostic.
- The bounded history window is a real `VecDeque` ring buffer, so eviction is
  O(1) at the front instead of an O(n) `Vec::remove(0)` shift.
- README states plainly that no benchmark result is currently published as
  verified evidence, rather than quoting a figure the repository cannot
  reproduce.

### Removed

- Legacy HTTP-proxy gate code, C FFI surface and `cbindgen` header generation.
- Private product documents, benchmark specifications and synthetic evidence
  generators from the public tree.
- Committed raw benchmark run directories and committed benchmark-derived
  result JSON.
- The experimental `microloop-compress` crate, which was never part of the
  public product surface.
- A repository-wide macOS linker override in `.cargo/config.toml`.
- `microloop.wrap` / `MonitoredAgent` / `RunReport`. The agent wrapper's
  duck-typed contract is not committed to as a stable API in this release; the
  host-owned loop over `Monitor` is the supported integration. See
  `docs/integration.md`.

## Unreleased (targeting 0.4.0)

### Added

- Adaptive runtime foundation. `Monitor.observe` now also returns `progress`
  (a `ProgressSnapshot`), `runtime` (the `RuntimeState` the host reported) and
  `recommendation` (a `RuntimeDecision`), while every legacy field keeps its
  meaning and default. This is Pass 1 of the transformation from a progress
  sensor into an adaptive execution runtime; no adaptation is performed yet.
- `Event.runtime` and the optional trajectory `runtime` field: `model`,
  `context_tokens`, `context_limit`, `input_tokens`, `output_tokens`, `cost`,
  `elapsed_seconds`, `tool_calls`, `remaining_budget`. All optional, so existing
  events remain valid.
- `RuntimeState`, `Usage`, `Budget`, `Capabilities`, `CapabilityLevel`,
  `ProgressSnapshot`, `RuntimeAction`, `RuntimeDecision`, `RuntimeController`,
  `RecommendationReason`, `RuntimeAdapter`, `MockAdapter`, `Episode` and
  `AdaptationRecord` in both the Rust core and the Python package.
- `RuntimeAction` defines the full action vocabulary (nine actions). Six are
  produced today -- `continue`, `replan`, `escalate_model`, `deescalate_model`,
  `compact_context` and `stop`; `restore_checkpoint`, `retry_tool` and
  `branch_strategy` remain experimental and are never recommended.
- `RuntimeController` is the new name for the policy abstraction. It reuses the
  existing cooldown, cap and hard step limit, adds budget enforcement and
  capability checking, and never recommends an action the adapter cannot
  perform. `Policy` remains as the stable compatibility surface.
- `microloop explain` renders the recommendation together with the runtime
  conditions it was made under. `inspect` and `replay` add a runtime block when
  a trajectory carries one.
- `Episode` and `AdaptationRecord` define the data model for the future learning
  loop: progress and runtime before an action, the action, and progress after.
  Nothing is trained from it yet.
- `ProgressState.Progressing` and `ProgressState.Uncertain` aliases for
  `healthy` and `warning`, so code can adopt the future names now.
- Adaptive actions (Pass 2). `replan`, `escalate_model`/`deescalate_model` and
  `compact_context` are now produced by a deterministic ladder: context pressure
  compacts first, otherwise the run replans, and only then escalates; sustained
  progress after an escalation de-escalates. Every rung is gated by adapter
  capabilities, per-step availability, a cooldown and the per-run cap.
- `RuntimeSession` closes the loop: it reports runtime state, applies the
  recommended adaptation through a `RuntimeAdapter`, and records what happened.
  It is opt-in, so the default posture stays advisory and the engine stays
  deterministic.
- `ApplyResult` and `RuntimeAdapter.can_apply`, so a runtime at the top of its
  model ladder can decline an action without Microloop knowing model names.
- `ControllerConfig` in Rust and matching rule knobs on the Python
  `RuntimeController`: `context_compaction_threshold`, `deescalate_after` and
  `escalate_cooldown`, alongside the per-state actions, cooldown, cap and step
  limit.
- `Monitor.observe(..., available=...)` and Rust `observe_with_available`:
  per-step actuator availability. Control actions (`continue`, `stop`) are never
  filtered.
- `AdaptationRecord.applied` and `adapter_reason` record whether the adapter
  actually performed an adaptation.
- Scored execution controller (Pass 3). The controller still uses
  `rules.candidates()` to decide what is *possible*, then gates, scores and picks
  the best action instead of taking the first rung. `ScoredController` is the
  explicit Python entry point (strategy `scored`); the Pass 2 ladder is kept
  unchanged as the `rule` baseline, never deleted.
- `ActionScore` and `ControllerTrace`: every scored decision reports the
  candidates it considered, their scores and the selected action. The trace is
  omitted from normal output and surfaced by `microloop replay --verbose` and in
  the `--json` views.
- `ActionScorer`/`HeuristicScorer` and `ScoringConfig` (with per-action
  evaluation horizons and `min_benefit`) as the seam a future `LearnedScorer`
  can replace without touching the controller. Scoring is four small terms --
  expected progress, cost, repetition and runtime pressure -- not one opaque
  weighted formula.
- Action outcome tracking. An action is judged after its evaluation horizon and
  recorded as an `ActionOutcome` (`improved`, `no_change`, `regressed`, or
  `pending`), and the actions attempted while stuck belong to a first-class
  `StallEpisode`. The controller penalises actions that already failed in the
  same episode rather than repeating them.
- `RecommendationReason::ActionExhausted`: when every actuator has been tried and
  failed, the controller recommends `continue` instead of cycling.
- Budget- and context-aware scoring: a nearly exhausted budget penalises
  escalation, context pressure boosts compaction, and sustained progress under a
  costlier model boosts de-escalation.
- Deterministic tie-breaking and a `min_benefit` stability margin, so the same
  input always yields the same action and the model does not oscillate.
- Real integrations (Pass 4). Provider code lives outside the package, in the
  repository's `integrations/` tree, so core stays provider-neutral.
  `integrations/coding_harness` is a complete agent loop (task, workspace, tools,
  tests, model call) with a pluggable provider; `integrations/openai_agents`
  registers the OpenAI Agents SDK lifecycle hooks without forking its runner.
- `ModelTier` (`fast`/`balanced`/`strong`): the provider-neutral model ladder.
  `TieredAdapter` maps tiers onto concrete model ids and performs the three
  adaptations for real -- it injects a replan message, switches tiers, and
  compacts the transcript with a deterministic `ContextCompactor`. No model name
  enters core.
- `ContextCompactor` and `Segment`: fixed-rule context compaction that keeps the
  task, plan, decisions, latest errors, verification state and files changed, and
  drops verbose, superseded or duplicated segments. Deterministic and replayable.
- `AdaptationResult` (one action's before/after/outcome) and `TaskOutcome`
  (`success`, `verifier`, `score`): the host's verdict on the task, which action
  outcomes are eventually judged against.
- `microloop.store.EpisodeStore`, a local SQLite store with one row per episode,
  per adaptation, and per candidate the scored controller considered. Counter-
  factual traces are persisted so the scorer can be audited later. Local only:
  no server, no account, no telemetry.
- `microloop stats [db] [--json]`: adaptations attempted, improved, no-change and
  regressed per action, plus cost per successful task.
- `Episode`/`AdaptationRecord` now retain each adaptation's step, model tier and
  controller trace.
- The static-vs-adaptive experiment (`integrations/experiment`): every task runs
  twice on the same workspace and provider, and the report compares task success,
  cost, tokens and steps per success. The default is an offline deterministic
  agent model labelled `run_mode="simulated"`; `--real` maps the tier ladder onto
  Anthropic model ids from the environment. Microloop is allowed to lose.
- `EpisodeStore.segmented()` and `EpisodeStore.attribution()`. `segmented()`
  groups adaptation outcomes by the situation they were chosen in, so "replan
  helped when the run was stalled and the error kept recurring" can be compared
  against "replan helped" in general. `attribution()` reports the share of runs
  that actually finished, per action, beside a control group of runs with no
  adaptation.
- `ControllerPolicy`, `POLICIES` and `run_sweep` (`--sweep`): run the static
  baseline and several controller configurations over the same task set and
  compare them pairwise. Every policy is printed, including the ones that lose.
- `microloop stats` now renders the per-arm table, the situation breakdown and
  the success attribution. The attribution table is labelled as confounded: an
  action usually fires on runs that were already in trouble, so it is read beside
  the arm table rather than instead of it.
- `AgentBehaviour` and `Ceiling` (`correct`, `plausible`, `hopeless`) on
  `Task`, and `BEHAVIOUR_MIX` in the task set: the stated prior over how an agent
  behaves when it gets stuck, fixed before any run. Between them the kinds cover
  every way an adaptive runtime can be right or wrong, including agents that
  recover unaided, agents that change approach and still land wrong, and agents
  no adaptation can reach.
- `SYSTEM_PROMPT` in the coding harness, and tool results returned to the model
  as proper `tool_result` blocks tied to the `tool_use` id the API issued. A real
  run previously never saw its own test output, so it had nothing to react to.

### Fixed

- The offline experiment was not an experiment. `ScriptedCodingProvider` (now
  `SimulatedCodingProvider`) applied the known fix only after spotting a
  Microloop-shaped phrase in the transcript, which made the static arm
  structurally incapable of success. It reported 0/40 against 40/40 -- a
  measurement of the harness, and one that could not have come out the other
  way. The simulated provider is now a model of an agent: it reacts to
  verification failures and to user instructions, with behaviour fixed per task
  and identical in both arms, so the comparison can be lost and is.
- The offline agent counted its own opening task prompt as an instruction, so
  every run started on the top rung of its approach ladder and succeeded on the
  first attempt.

### Measured

The first real 40-task run of the offline experiment, paired per task against
doing nothing:

| policy | success | won | lost | p |
|---|---|---|---|---|
| static (no adaptation) | 21/40 | — | — | — |
| eager replan | 18/40 | 0 | 3 | 0.250 |
| patient replan | 22/40 | 1 | 0 | 1.000 |
| wary replan | 16/40 | 0 | 5 | 0.062 |

With the shipped default policy the adaptive arm was **worse** than doing
nothing. Replanning on every stalled step interrupts an agent that is slowly
grinding toward a fix; the agent abandons the approach it has barely tried, loses
the ground it made and runs out of budget.

No row clears p<0.05, and the sweep reports that itself rather than naming a
winner. With at most five tasks where any policy disagreed with the baseline, one
more discordant task would be needed before a sign test could reach significance
at all. The experiment is underpowered, not conclusive. It rules out a large
effect in either direction and shows that intervening as fast as a run stalls is
not free.

Two findings about the measurement itself:

- The `improved` label on an adaptation is close to useless as evidence.
  **95% of replans were scored `improved` while the adaptive arm was losing**,
  because progress recovers locally and the task still fails. That is why
  `attribution()` exists and why it reports success rates beside a control.
- The task set was missing the population that makes the comparison meaningful.
  Tasks that are easy or lost outright cannot be decided by intervention timing,
  so a runtime that intervened on every stalled step scored exactly the same as
  one that never intervened. A `close` kind was added: solvable agents with only a
  few steps to spare, where the steps an unnecessary adaptation wastes decide the
  run either way. Adding it changed the ranking of the policies entirely, which
  is the clearest evidence that the previous ordering was noise.

This is not evidence about real models. The offline agent is a model of an agent.
The `--real` path is the one that would say something about real models, and it
has been smoke-tested against a stub client but not run against the API.

### Changed

- `RuntimeController` owns action selection (a deterministic ladder in
  `runtime/rules.rs`) instead of delegating to `Policy`, so it can space and
  order several actuators. `Policy` and `PolicyConfig` are unchanged and still
  convert into a controller.
- `microloop explain` reports the selected actuator and its reason, and (with
  `replay --verbose`) the candidates it scored.
- `RuntimeController` gained a `strategy` (`rule` or `scored`) and a `scoring`
  block. The default is the existing rule behaviour, so this is additive.
- `Episode` records an outcome per adaptation (`result`: `improved`,
  `no_change`, `regressed`), closing each action at the progress it left the run
  in.
- `Monitor` accepts `controller`, `capabilities` and `budget` in addition to
  `policy`. `inspect`'s `--json` report and `replay`'s JSONL now include
  `progress`, `runtime` and `recommendation` per step.

### Planned

- `ProgressState.healthy` and `ProgressState.warning` are likely to become
  `progressing` and `uncertain`. Both current names are monitoring vocabulary;
  the replacements describe trajectory dynamics instead. `healthy` in
  particular implies a health check rather than observed forward movement.
  `ProgressState` is part of the public API, so this will be a breaking change
  when it lands.
- `Decision.severity` is soft-deprecated. It is a fixed lookup over `status`
  and carries no information beyond it. It stays in 0.3 for compatibility and
  will be removed in 0.4.
- Documentation reframed around progress and recommendation rather than
  detection and intervention. The detectors, the policy and the API surface are
  unchanged.
- The README now carries measured per-step cost and memory figures instead of
  the old unmeasured latency claim. `benchmarks/perf.py` and `make perf`
  reproduce them; the script measures the engine, not agent performance.
- Added a short section answering why this is not a prompt, and a disambiguating
  line separating Microloop from the agent it observes.
- Doc titles are now verb phrases naming what the reader gets, and llms.txt is a
  machine-readable index of the documentation.
- `microloop inspect` no longer renders a Decision for a terminal. It tells the
  trajectory as a story: only meaningful transitions, in plain language, then one
  outcome line. The fixed-width Status/Worst at/Recovered/Evidence/Action block is
  gone, as is the word "recovered", which implied Microloop caused a recovery it
  did not cause in observe-only mode. Internal states read as progressing,
  uncertain, stalled and regressing; reason enums are no longer shown by default.
  Adds `--verbose` and `--json` layers.
- `microloop replay` is now the timeline: every step, with evidence rendered
  underneath and `->` marking a recommendation. `inspect` says what happened,
  `replay` shows how. Its `--json` output now includes evidence.

## [0.2.0] (2026-07-01)

- Experimental trajectory monitoring and policy engine.
