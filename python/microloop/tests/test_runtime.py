"""Runtime layer: runtime state, controller, capabilities, adapter and episode."""

import microloop
from microloop import (
    ActionOutcome,
    ActionScore,
    ApplyResult,
    Budget,
    Capabilities,
    CapabilityLevel,
    ControllerTrace,
    Decision,
    Episode,
    Event,
    InterventionAction,
    MockAdapter,
    Monitor,
    Policy,
    ProgressState,
    RuntimeAction,
    RuntimeAdapter,
    RuntimeController,
    RuntimeSession,
    RuntimeState,
    ScoredController,
    Strategy,
)


def _failing(monitor: Monitor, step: int, *, context_tokens: int = 42_000):
    return monitor.observe(
        action="pytest tests/",
        observation="1 failed, 4 passed",
        state={"git_head": "abc123"},
        metrics={"exit_code": 1.0},
        metadata={"error": "AssertionError: test_admin.py:42"},
        runtime={
            "model": "sonnet",
            "context_tokens": context_tokens,
            "context_limit": 64_000,
            "cost": 0.82,
        },
        step=step,
    )


def _opt_in() -> Policy:
    return Policy(
        stalled=InterventionAction.Replan,
        regressing=InterventionAction.Stop,
        cooldown_steps=1,
        max_interventions=5,
    )


def _adaptive(**overrides) -> RuntimeController:
    kwargs = {
        "stalled": InterventionAction.Replan,
        "cooldown_steps": 1,
        "max_interventions": 10,
    }
    kwargs.update(overrides)
    return RuntimeController(**kwargs)


def test_pass_one_exit_criteria() -> None:
    """The contract Pass 1 has to make true."""
    monitor = Monitor(policy=_opt_in())
    result = None
    for step in range(1, 4):
        result = _failing(monitor, step)

    assert result is not None
    assert result.progress.state == "stalled"
    assert result.runtime.model == "sonnet"
    assert result.recommendation.action == "replan"
    assert result.recommendation.reason == "trajectory_stalled"
    assert result.runtime.context_utilization == 42_000 / 64_000


def test_legacy_v03_usage_is_unchanged() -> None:
    """Existing code that only reads status/intervention keeps working."""
    monitor = Monitor()
    for step in range(1, 3):
        _failing(monitor, step)
    decision = _failing(monitor, 3)

    assert decision.status == ProgressState.Stalled
    assert decision.intervention == InterventionAction.Observe
    assert not decision.should_intervene
    assert "repeated_error" in decision.reasons
    assert decision.progress.state == "stalled"
    assert decision.recommendation.action == RuntimeAction.Continue
    assert decision.recommendation.reason == "observation_only"


def test_progress_snapshot_tracks_when_the_state_began() -> None:
    monitor = Monitor()
    for step in range(1, 3):
        _failing(monitor, step)
    decision = _failing(monitor, 3)
    assert decision.progress.since_step == 3
    assert decision.progress.degraded


def test_progress_state_dynamic_aliases() -> None:
    assert ProgressState.Progressing == "healthy"
    assert ProgressState.Uncertain == "warning"


def test_budget_exhaustion_stops_even_in_observe_only_mode() -> None:
    monitor = Monitor(budget=Budget(max_cost=1.0))
    decision = monitor.observe("run", "out", runtime=RuntimeState(cost=1.25))
    assert decision.recommendation.action == RuntimeAction.Stop
    assert decision.recommendation.reason == "budget_exhausted"
    assert decision.intervention == InterventionAction.Stop


def test_unsupported_action_falls_back_to_continue() -> None:
    monitor = Monitor(
        policy=_opt_in(),
        capabilities=Capabilities(replan=False),
    )
    for step in range(1, 3):
        _failing(monitor, step)
    decision = _failing(monitor, 3)
    assert decision.recommendation.action == RuntimeAction.Continue
    assert decision.recommendation.reason == "action_unsupported"


def test_controller_can_decide_independently() -> None:
    monitor = Monitor()
    for step in range(1, 3):
        _failing(monitor, step)
    decision = _failing(monitor, 3)

    recommendation = _adaptive().decide(decision)
    assert recommendation.action == RuntimeAction.Replan
    assert recommendation.reason == "trajectory_stalled"
    assert recommendation.should_intervene


def test_monitor_accepts_a_controller() -> None:
    monitor = Monitor(controller=_adaptive())
    for step in range(1, 3):
        _failing(monitor, step)
    assert _failing(monitor, 3).recommendation.action == RuntimeAction.Replan


def test_runtime_state_derived_values() -> None:
    state = RuntimeState(
        model="sonnet",
        context_tokens=48_000,
        context_limit=64_000,
        input_tokens=100,
        output_tokens=20,
        cost=0.5,
    )
    assert state.context_utilization == 0.75
    assert state.total_tokens == 120
    assert state.estimated_cost == 0.5
    assert state.usage().cost == 0.5
    assert RuntimeState.from_dict({"estimated_cost": 1.5}).cost == 1.5


def test_runtime_action_vocabulary() -> None:
    for action in (
        RuntimeAction.RestoreCheckpoint,
        RuntimeAction.RetryTool,
        RuntimeAction.BranchStrategy,
    ):
        assert RuntimeAction.is_experimental(action)
    for action in (
        RuntimeAction.Continue,
        RuntimeAction.Replan,
        RuntimeAction.EscalateModel,
        RuntimeAction.DeescalateModel,
        RuntimeAction.CompactContext,
        RuntimeAction.Stop,
    ):
        assert RuntimeAction.is_enabled(action)


def test_capabilities_gate_actions() -> None:
    assert Capabilities().supports(RuntimeAction.Replan)
    assert not Capabilities().supports(RuntimeAction.EscalateModel)
    assert Capabilities(model_switch=True).supports(RuntimeAction.EscalateModel)
    assert Capabilities().supports(RuntimeAction.Stop)


def test_mock_adapter_satisfies_the_protocol() -> None:
    adapter = MockAdapter(
        capabilities=Capabilities(model_switch=True), snapshot=RuntimeState(model="sonnet")
    )
    assert isinstance(adapter, RuntimeAdapter)
    assert adapter.capabilities().supports(RuntimeAction.EscalateModel)
    assert adapter.snapshot().model == "sonnet"
    adapter.apply(RuntimeAction.Replan)
    assert adapter.applied == ["replan"]


def test_apply_result_reports_what_happened() -> None:
    result = MockAdapter().apply(RuntimeAction.Replan)
    assert isinstance(result, ApplyResult)
    assert result.applied is True
    assert result.action == "replan"


def test_episode_records_adaptations_and_cost_per_success() -> None:
    monitor = Monitor(policy=_opt_in())
    episode = Episode(goal="fix auth")
    for step in range(1, 4):
        episode.record(_failing(monitor, step))
    episode.finish("completed")

    summary = episode.summary()
    assert summary["goal"] == "fix auth"
    assert summary["outcome"] == "completed"
    assert summary["steps"] == 3
    assert summary["usage"]["cost"] == 0.82
    assert summary["cost_per_success"] == 0.82
    assert [record["action"] for record in summary["adaptations"]] == ["replan"]


def test_episode_without_known_outcome_has_no_cost_per_success() -> None:
    monitor = Monitor()
    episode = Episode.from_decisions([_failing(monitor, 1)])
    assert episode.summary()["cost_per_success"] is None


def test_episode_records_each_adaptation_separately() -> None:
    controller = _adaptive(
        escalate_cooldown=1,
        deescalate_after=1,
        capabilities=Capabilities(model_switch=True),
    )
    monitor = Monitor(controller=controller)
    episode = Episode()
    for step in range(1, 4):
        episode.record(_failing(monitor, step))
    episode.record(_failing(monitor, 10))
    episode.record(
        monitor.observe(action="read src/auth.py", observation="contents", step=11)
    )
    episode.finish("completed")

    assert [record.action for record in episode.adaptations] == [
        "replan",
        "escalate_model",
        "deescalate_model",
    ]
    assert episode.adaptations[0].after["progress"] == "stalled"
    assert episode.adaptations[1].after["progress"] == "healthy"
    # every adaptation carries the runtime snapshot it was judged in
    assert episode.adaptations[0].after["runtime"]["model"] == "sonnet"


def test_event_runtime_flows_into_the_decision() -> None:
    monitor = Monitor()
    event = Event(step=1, action="run", observation="out", runtime=RuntimeState(model="haiku"))
    decision = monitor.observe_event(event)
    assert isinstance(decision, Decision)
    assert decision.runtime is not None
    assert decision.runtime.model == "haiku"


def test_capability_level_tracks_exposed_data() -> None:
    assert Event(1, "a", "o").capability_level() == CapabilityLevel.Signals
    assert Event(1, "a", "o", state={"k": "v"}).capability_level() == CapabilityLevel.Progress
    runtime_event = Event(1, "a", "o", runtime=RuntimeState(model="sonnet"))
    assert runtime_event.capability_level() == CapabilityLevel.Runtime


# --- Pass 2: the actuators ---------------------------------------------------


def test_context_pressure_compacts_before_replanning() -> None:
    controller = _adaptive(capabilities=Capabilities(context_compaction=True))
    monitor = Monitor(controller=controller)
    result = None
    for step in range(1, 4):
        result = _failing(monitor, step, context_tokens=60_000)

    assert result is not None
    assert result.recommendation.action == RuntimeAction.CompactContext
    assert result.recommendation.reason == "context_pressure"


def test_stalled_run_replans_then_escalates() -> None:
    controller = _adaptive(
        escalate_cooldown=1, capabilities=Capabilities(model_switch=True)
    )
    monitor = Monitor(controller=controller)
    for step in range(1, 3):
        _failing(monitor, step)
    first = _failing(monitor, 3)
    assert first.recommendation.action == RuntimeAction.Replan

    second = _failing(monitor, 10)
    assert second.recommendation.action == RuntimeAction.EscalateModel
    assert second.recommendation.reason == "trajectory_stalled"


def test_sustained_progress_deescalates_after_an_escalation() -> None:
    controller = _adaptive(
        escalate_cooldown=1,
        deescalate_after=1,
        capabilities=Capabilities(model_switch=True),
    )
    monitor = Monitor(controller=controller)
    for step in range(1, 3):
        _failing(monitor, step)
    _failing(monitor, 3)
    assert _failing(monitor, 10).recommendation.action == RuntimeAction.EscalateModel

    decision = monitor.observe(action="read src/auth.py", observation="contents", step=11)
    assert decision.progress.state == "healthy"
    assert decision.recommendation.action == RuntimeAction.DeescalateModel
    assert decision.recommendation.reason == "progress_recovered"


def test_available_gates_actuators_but_never_control() -> None:
    monitor = Monitor(controller=_adaptive())
    decision = None
    for step in range(1, 4):
        decision = monitor.observe(
            action="pytest tests/",
            observation="1 failed, 4 passed",
            metrics={"exit_code": 1.0},
            metadata={"error": "E"},
            step=step,
            available=set(),
        )
    assert decision is not None
    assert decision.recommendation.action == RuntimeAction.Continue
    assert decision.recommendation.reason == "action_unsupported"

    stopping = Monitor(budget=Budget(max_cost=1.0)).observe(
        "run", "out", runtime=RuntimeState(cost=2.0), available=set()
    )
    assert stopping.recommendation.action == RuntimeAction.Stop


def test_runtime_session_applies_adaptation_and_records_it() -> None:
    adapter = MockAdapter(snapshot=RuntimeState(model="sonnet", cost=0.4))
    session = RuntimeSession(Monitor(controller=_adaptive()), adapter)
    for step in range(1, 4):
        session.observe(
            action="pytest tests/",
            observation="1 failed, 4 passed",
            metrics={"exit_code": 1.0},
            metadata={"error": "E"},
            step=step,
        )

    assert adapter.applied == [RuntimeAction.Replan]
    assert session.adaptations[0].action == RuntimeAction.Replan
    assert session.adaptations[0].applied is True

    session.finish("completed")
    assert session.summary()["cost_per_success"] == 0.4


def test_runtime_session_never_recommends_an_unavailable_action() -> None:
    adapter = MockAdapter(unavailable={RuntimeAction.Replan})
    session = RuntimeSession(Monitor(controller=_adaptive()), adapter)
    decision = None
    for step in range(1, 4):
        decision = session.observe(
            action="pytest tests/",
            observation="1 failed, 4 passed",
            metrics={"exit_code": 1.0},
            metadata={"error": "E"},
            step=step,
        )

    assert decision is not None
    assert decision.recommendation.action == RuntimeAction.Continue
    assert decision.recommendation.reason == "action_unsupported"
    assert adapter.applied == []


def test_runtime_session_default_observes_only() -> None:
    adapter = MockAdapter()
    session = RuntimeSession(Monitor(), adapter)
    for step in range(1, 4):
        session.observe(
            action="pytest tests/",
            observation="1 failed",
            metrics={"exit_code": 1.0},
            metadata={"error": "E"},
            step=step,
        )
    assert adapter.applied == []


def test_runtime_types_are_public() -> None:
    for name in (
        "ActionOutcome",
        "ActionScore",
        "ApplyResult",
        "Budget",
        "Capabilities",
        "CapabilityLevel",
        "Episode",
        "MockAdapter",
        "ProgressSnapshot",
        "RuntimeAction",
        "RuntimeController",
        "RuntimeDecision",
        "RuntimeSession",
        "RuntimeState",
        "ScoredController",
        "Strategy",
        "ControllerTrace",
        "Usage",
    ):
        assert name in microloop.__all__


# --- Pass 3: the scored controller -------------------------------------------


def _scored(**overrides) -> ScoredController:
    kwargs = {
        "stalled": InterventionAction.Replan,
        "regressing": InterventionAction.Stop,
        "cooldown_steps": 1,
        "max_interventions": 100,
        "capabilities": Capabilities(model_switch=True, context_compaction=True),
    }
    kwargs.update(overrides)
    return ScoredController(**kwargs)


def test_scored_controller_compacts_under_context_pressure() -> None:
    monitor = Monitor(controller=_scored())
    result = None
    for step in range(1, 4):
        result = _failing(monitor, step, context_tokens=59_000)
    assert result is not None
    assert result.recommendation.action == RuntimeAction.CompactContext
    assert result.recommendation.reason == "context_pressure"


def test_scored_controller_emits_a_trace() -> None:
    monitor = Monitor(controller=_scored())
    for step in range(1, 3):
        _failing(monitor, step)
    decision = _failing(monitor, 3)
    trace = decision.recommendation.trace
    assert isinstance(trace, ControllerTrace)
    assert trace.strategy == Strategy.Scored
    assert trace.selected == RuntimeAction.Replan
    assert len(trace.candidates) >= 3
    assert all(isinstance(candidate, ActionScore) for candidate in trace.candidates)
    best = max(trace.candidates, key=lambda candidate: candidate.score)
    assert best.action == RuntimeAction.Replan


def test_rule_controller_emits_no_trace() -> None:
    monitor = Monitor(controller=_adaptive())
    for step in range(1, 3):
        _failing(monitor, step)
    assert _failing(monitor, 3).recommendation.trace is None


def test_scored_controller_escalates_after_replan_fails() -> None:
    monitor = Monitor(controller=_scored())
    for step in range(1, 3):
        _failing(monitor, step)
    assert _failing(monitor, 3).recommendation.action == RuntimeAction.Replan
    assert _failing(monitor, 6).recommendation.action == RuntimeAction.EscalateModel


def test_scored_controller_respects_a_short_budget() -> None:
    monitor = Monitor(controller=_scored(budget=Budget(max_cost=1.0)))
    for step in range(1, 3):
        _failing(monitor, step)
    decision = _failing(monitor, 3)
    assert decision.recommendation.action != RuntimeAction.EscalateModel


def test_action_outcome_is_recorded_on_adaptations() -> None:
    monitor = Monitor(controller=_scored())
    episode = Episode()
    for step in range(1, 4):
        episode.record(_failing(monitor, step))
    # step 6: the replan from step 3 is judged no-change, and escalate is chosen
    episode.record(_failing(monitor, 6))
    # step 7: progress recovers, closing the escalate adaptation as improved
    episode.record(monitor.observe(action="read x", observation="y", step=7))
    episode.finish("completed")

    by_action = {record.action: record for record in episode.adaptations}
    assert by_action["replan"].result == ActionOutcome.NoChange
    assert by_action["escalate_model"].result == ActionOutcome.Improved


def test_scored_controller_reports_action_exhaustion() -> None:
    monitor = Monitor(controller=_scored())
    for step in range(1, 3):
        _failing(monitor, step)
    _failing(monitor, 3)  # replan
    _failing(monitor, 6)  # escalate
    decision = _failing(monitor, 12)
    assert decision.recommendation.action == RuntimeAction.Continue
    assert decision.recommendation.reason == "action_exhausted"


def test_scored_controller_is_explicit_about_its_strategy() -> None:
    scored = _scored()
    assert scored.strategy == Strategy.Scored
    assert scored.config["strategy"] == Strategy.Scored
    rule = _adaptive()
    assert rule.config["strategy"] == Strategy.Rule
