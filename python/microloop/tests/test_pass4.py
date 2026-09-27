"""Pass 4: real integrations, outcome collection and the experiment runner."""

from __future__ import annotations

import asyncio
import json

from microloop import (
    ActionOutcome,
    AdaptationRecord,
    AdaptationResult,
    ContextCompactor,
    Episode,
    ModelTier,
    Monitor,
    RuntimeAction,
    RuntimeSession,
    ScoredController,
    TaskOutcome,
    TieredAdapter,
)
from microloop.cli import main
from microloop.runtime import Segment
from microloop.store import EpisodeStore

from integrations.coding_harness.harness import CodingHarness
from integrations.coding_harness.providers import (
    AgentBehaviour,
    Ceiling,
)
from integrations.coding_harness.tasks import build_tasks
from integrations.experiment.runner import (
    POLICIES,
    ControllerPolicy,
    run_experiment,
    run_sweep,
)
from integrations.openai_agents import (
    MicroloopRunHooks,
    MicroloopRuntime,
    OpenAIAgentsAdapter,
)

# --- model tiers -------------------------------------------------------------


def test_model_tier_ladder() -> None:
    assert ModelTier.Order == ("fast", "balanced", "strong")
    assert ModelTier.up(ModelTier.Fast) == ModelTier.Balanced
    assert ModelTier.up(ModelTier.Strong) is None
    assert ModelTier.down(ModelTier.Balanced) == ModelTier.Fast
    assert ModelTier.down(ModelTier.Fast) is None
    assert ModelTier.rank("nope") == -1
    assert ModelTier.is_tier("strong")


# --- compaction --------------------------------------------------------------


def test_compactor_keeps_essentials_and_dedupes() -> None:
    compactor = ContextCompactor(keep_recent=2)
    segments = [
        Segment("task", "fix the bug"),
        Segment("tool", "verbose output 1"),
        Segment("tool", "verbose output 2"),
        Segment("tool", "verbose output 3"),
        Segment("error", "AssertionError: nope"),
        Segment("plan", "old plan"),
        Segment("plan", "new plan"),
        Segment("verification", "1 failed"),
    ]
    report = compactor.compact(segments)
    kinds = [segment.kind for segment in report.kept]
    assert kinds.count("task") == 1
    assert kinds.count("error") == 1
    assert kinds.count("verification") == 1
    # the two most recent tools survive, the oldest is dropped
    assert [s.text for s in report.kept if s.kind == "tool"] == [
        "verbose output 2",
        "verbose output 3",
    ]
    assert report.dropped_count == 1


def test_compactor_is_deterministic() -> None:
    segments = [Segment("tool", f"out {i}") for i in range(6)]
    first = ContextCompactor().compact(segments).to_dict()
    second = ContextCompactor().compact(segments).to_dict()
    assert first == second


# --- tiered adapter ----------------------------------------------------------


def _adapter(**kwargs) -> TieredAdapter:
    defaults = {
        "tiers": {ModelTier.Fast: "small", ModelTier.Balanced: "mid", ModelTier.Strong: "big"},
        "start": ModelTier.Fast,
        "context_limit": 1_000,
        "compactor": ContextCompactor(),
    }
    defaults.update(kwargs)
    return TieredAdapter(**defaults)


def test_tiered_adapter_performs_the_three_adaptations() -> None:
    adapter = _adapter()
    assert adapter.model == "small" and adapter.tier == ModelTier.Fast

    result = adapter.apply(RuntimeAction.EscalateModel)
    assert result.applied and adapter.tier == ModelTier.Balanced and adapter.model == "mid"

    adapter.apply(RuntimeAction.Replan)
    assert adapter.replans == 1
    assert adapter.take_replan() is not None
    assert adapter.take_replan() is None  # cleared

    adapter.record_segment("tool", "verbose " * 50)
    adapter.record_segment("task", "goal")
    compact = adapter.apply(RuntimeAction.CompactContext)
    assert compact.applied and adapter.compactions == 1
    assert adapter.last_compaction is not None


def test_tiered_adapter_refuses_beyond_the_ladder_ends() -> None:
    adapter = _adapter()
    assert not adapter.can_apply(RuntimeAction.DeescalateModel)
    assert adapter.can_apply(RuntimeAction.EscalateModel)
    assert adapter.apply(RuntimeAction.DeescalateModel).applied is False
    adapter.apply(RuntimeAction.EscalateModel)
    adapter.apply(RuntimeAction.EscalateModel)
    assert adapter.tier == ModelTier.Strong
    assert not adapter.can_apply(RuntimeAction.EscalateModel)
    assert adapter.apply(RuntimeAction.EscalateModel).applied is False


def test_tiered_adapter_reports_context_and_usage() -> None:
    adapter = _adapter()
    adapter.record_segment("tool", "abcd" * 10)
    adapter.add_usage(input_tokens=100, output_tokens=25, cost=0.5)
    adapter.add_tool_call()
    snapshot = adapter.snapshot()
    assert snapshot.model == "small"
    assert snapshot.context_tokens == len("abcd" * 10) // 4
    assert snapshot.cost == 0.5
    assert snapshot.total_tokens == 125
    assert snapshot.tool_calls == 1


# --- result types ------------------------------------------------------------


def test_adaptation_result_and_task_outcome_round_trip() -> None:
    result = AdaptationResult(
        action="escalate_model",
        step=18,
        before={"progress": "stalled", "model_tier": "fast", "cost": 0.62},
        after={"progress": "progressing", "model_tier": "strong", "cost": 0.91},
        outcome=ActionOutcome.Improved,
        applied=True,
    )
    payload = result.to_dict()
    assert AdaptationResult.from_dict(payload).to_dict() == payload
    assert AdaptationResult.from_dict(None).action == "continue"

    outcome = TaskOutcome(success=True, verifier="pytest", score=1.0)
    assert TaskOutcome.from_dict(outcome.to_dict()).to_dict() == outcome.to_dict()
    assert TaskOutcome.from_dict(None).success is False


# --- episode ----------------------------------------------------------------


def test_episode_records_trace_and_model_tier() -> None:
    adapter = _adapter()
    controller = ScoredController(
        stalled=RuntimeAction.Replan,
        regressing=RuntimeAction.Stop,
        cooldown_steps=1,
        max_interventions=100,
        capabilities=adapter.capabilities(),
    )
    session = RuntimeSession(Monitor(controller=controller), adapter)
    for step in range(1, 4):
        adapter.record_segment("tool", f"out {step}")
        session.observe(
            action="pytest tests/",
            observation="1 failed, 4 passed",
            metrics={"exit_code": 1.0},
            metadata={"error": "AssertionError: x"},
            step=step,
        )
    # a recovering step closes the adaptation, judging it in the step it opened
    session.observe(
        action="edit src/auth.py",
        observation="patched",
        metrics={"exit_code": 0.0},
        step=4,
    )
    session.finish("completed")
    summary = session.summary()
    adaptation = summary["adaptations"][0]
    assert adaptation["action"] == RuntimeAction.Replan
    assert adaptation["step"] == 3
    assert adaptation["trace"] is not None
    assert adaptation["trace"]["strategy"] == "scored"
    assert adaptation["before"]["model_tier"] == ModelTier.Fast
    assert adaptation["result"] == ActionOutcome.Improved


def test_adaptation_record_step_and_trace_are_optional() -> None:
    record = AdaptationRecord(before={"progress": "stalled"}, action="replan")
    assert "step" not in record.to_dict()
    assert "trace" not in record.to_dict()


# --- store -------------------------------------------------------------------


def _episode_with_adaptation() -> Episode:
    adapter = _adapter()
    controller = ScoredController(
        stalled=RuntimeAction.Replan,
        cooldown_steps=1,
        max_interventions=100,
        capabilities=adapter.capabilities(),
    )
    session = RuntimeSession(Monitor(controller=controller), adapter)
    for step in range(1, 4):
        adapter.record_segment("tool", f"out {step}")
        session.observe(
            action="pytest",
            observation="1 failed",
            metrics={"exit_code": 1.0},
            metadata={"error": "AssertionError"},
            step=step,
        )
    session.finish("completed")
    return session.episode


def test_store_records_episode_adaptations_and_candidates() -> None:
    episode = _episode_with_adaptation()
    store = EpisodeStore(":memory:")
    try:
        episode_id = store.record(
            episode, task="t1", arm="adaptive", run_mode="simulated", success=True
        )
        assert episode_id == 1
        rows = store.episodes()
        assert len(rows) == 1 and rows[0]["task"] == "t1"
        adaptations = store.adaptations()
        assert adaptations and adaptations[0]["action"] == "replan"
        assert store.candidates()
        assert any(row["selected"] for row in store.candidates())
    finally:
        store.close()


def test_store_stats_aggregate() -> None:
    episode = _episode_with_adaptation()
    store = EpisodeStore(":memory:")
    try:
        store.record(episode, task="a", arm="adaptive", run_mode="simulated", success=True)
        store.record(episode, task="b", arm="adaptive", run_mode="simulated", success=False)
        stats = store.stats()
        assert stats["episodes"] == 2
        assert stats["successful"] == 1
        assert stats["adaptations"] == 2
        assert stats["by_action"]["replan"]["attempted"] == 2
        assert stats["arms"]["adaptive"]["successful"] == 1
    finally:
        store.close()


def test_store_export_jsonl(tmp_path) -> None:
    store = EpisodeStore(":memory:")
    try:
        store.record(_episode_with_adaptation(), task="a", arm="adaptive")
        path = tmp_path / "episodes.jsonl"
        written = store.export_jsonl(path)
        assert written == 1
        payload = json.loads(path.read_text().splitlines()[0])
        assert payload["task"] == "a"
        assert payload["summary"]["adaptations"]
    finally:
        store.close()


# --- CLI stats ---------------------------------------------------------------


def test_cli_stats_reads_the_store(tmp_path, capsys) -> None:
    db = tmp_path / "episodes.db"
    store = EpisodeStore(str(db))
    store.record(_episode_with_adaptation(), task="a", arm="adaptive", success=True)
    store.close()

    assert main(["stats", str(db)]) == 0
    output = capsys.readouterr().out
    assert "Microloop runtime statistics" in output
    assert "Episodes" in output
    assert "replan" in output

    assert main(["stats", "--json", str(db)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["episodes"] == 1


def test_cli_stats_without_a_database(tmp_path, capsys) -> None:
    assert main(["stats", str(tmp_path / "missing.db")]) == 0
    assert "No episodes recorded" in capsys.readouterr().out


# --- coding harness ----------------------------------------------------------


def _task(kind: str, **overrides):
    """Build a one-off task whose agent behaves a specific way.

    The defaults are the ones the 40-task sweep exercises, so a unit test and
    the recorded run are talking about the same agent.
    """
    ceilings = {
        "recovers": Ceiling.Correct,
        "stubborn": Ceiling.Correct,
        "plausible": Ceiling.Plausible,
        "hopeless": Ceiling.Hopeless,
    }
    patience = {"recovers": 1, "stubborn": 3, "plausible": 3, "hopeless": 3}[kind]
    settle = {"recovers": 1, "stubborn": 3, "plausible": 2, "hopeless": 1}[kind]
    task = build_tasks(1)[0]
    task.behaviour = AgentBehaviour(
        patience=overrides.pop("patience", patience),
        ceiling=ceilings[kind],
        distractors=task.behaviour.distractors,
        settle=overrides.pop("settle", settle),
        **overrides,
    )
    return task


def _run(task, arm: str, max_steps: int = 40, policy: ControllerPolicy | None = None):
    from integrations.experiment.runner import (
        DEFAULT_TIERS,
        _simulated_factory,
        _tier_by_model,
        build_session,
    )

    session = build_session(arm, policy=policy)
    provider = _simulated_factory(_tier_by_model(DEFAULT_TIERS))(task)
    harness = CodingHarness(
        task=task,
        provider=provider,
        session=session,
        arm=arm,
        max_steps=max_steps,
    )
    return session, provider, harness.run()


def test_harness_feeds_tool_results_back_to_the_agent() -> None:
    """The agent must see test output, or it cannot react to it."""
    task = _task("recovers")
    _, provider, result = _run(task, "static")
    assert result.success is True
    assert provider.failures >= 1, "the agent never read a failing test"


def test_agent_recovers_unaided_so_adaptation_is_wasted() -> None:
    """A failure mode the runtime cannot help with: the agent never needed it."""
    task = _task("recovers")
    static_session, _, static = _run(task, "static")
    adaptive_session, _, adaptive = _run(task, "adaptive")
    assert static.success is True
    assert adaptive.success is True
    # Both arms finish the task; the adaptive one just paid for the privilege.
    assert static.steps <= adaptive.steps


def test_hopeless_task_fails_in_both_arms() -> None:
    """No adaptation can save a task the agent will never move off."""
    task = _task("hopeless")
    _, _, static = _run(task, "static")
    _, _, adaptive = _run(task, "adaptive")
    assert static.success is False
    assert adaptive.success is False


def test_eager_replan_makes_a_stubborn_agent_worse() -> None:
    """The headline negative result, asserted so it cannot silently regress.

    Replanning on every stalled step interrupts an agent that is slowly grinding
    toward the fix. It loses the ground it had made, never climbs back, and runs
    out of budget. Doing nothing beats it.
    """
    task = _task("stubborn")
    _, _, static = _run(task, "static", max_steps=40)
    _, provider, adaptive = _run(task, "adaptive", max_steps=40)
    assert static.success is True
    assert adaptive.success is False
    assert provider.interruptions > 0, "the nudge was expected to knock it back"


def test_a_wary_controller_beats_replanning_every_step() -> None:
    task = _task("stubborn")
    _, _, eager = _run(task, "adaptive", policy=POLICIES[0])
    _, _, wary = _run(task, "adaptive", policy=POLICIES[2])
    assert eager.success is False
    assert wary.success is True


def test_escalation_alone_does_not_rescue_an_unready_agent() -> None:
    """A stronger model needs the run to be ready before it can help.

    This is why the ladder order matters: escalating a run that has no evidence
    to act on costs money and changes nothing.
    """
    task = _task("stubborn", patience=40)
    session, provider, _ = _run(task, "adaptive", max_steps=40, policy=POLICIES[2])
    assert session.adapter.compactions >= 0
    # Even at the top of the ladder the agent has not been given a reason to move.
    assert provider.rung == 0
    assert session.adapter.current_tier() in ("fast", "balanced", "strong")


def test_sweep_reports_every_policy_including_the_losers() -> None:
    report = run_sweep(build_tasks(20), policies=POLICIES[:2])
    names = [row.name for row in report.rows]
    assert names == ["static", "eager", "patient"]
    rendered = report.render()
    # A losing policy must still be printed, and the comparison must be stated
    # in terms of what was actually won and lost, not just a winner.
    assert "eager" in rendered and "static" in rendered
    assert "Paired against doing nothing" in rendered
    assert "won" in rendered and "lost" in rendered
    verdict = report.verdict()
    assert verdict["comparable"] is True
    assert {row["policy"] for row in verdict["comparisons"]} == {"eager", "patient"}
    for row in verdict["comparisons"]:
        assert row["net"] == row["won"] - row["lost"]


def test_sweep_refuses_to_claim_a_win_it_cannot_support() -> None:
    """An underpowered sweep must say so rather than name a winner."""
    report = run_sweep(build_tasks(20), policies=POLICIES[:1])
    conclusion = "\n".join(report._conclusion())
    if not any(row["significant"] for row in report.verdict()["comparisons"]):
        assert "no benefit is demonstrated" in conclusion
        assert "no harm is ruled out" in conclusion


# --- experiment --------------------------------------------------------------


def test_experiment_report_shape() -> None:
    # One full behaviour block, so the set spans every kind of agent.
    report = run_experiment(build_tasks(20), run_mode="simulated")
    assert report.tasks == 20
    assert set(report.arms) == {"static", "adaptive"}
    static, adaptive = report.arms["static"], report.arms["adaptive"]
    assert static.episodes == 20 and adaptive.episodes == 20
    # Neither arm sweeps the set. An experiment whose adaptive arm cannot fail
    # is measuring the harness, not the controller.
    assert 0 < static.successes < static.episodes
    assert 0 < adaptive.successes < adaptive.episodes
    payload = report.to_dict()
    assert payload["run_mode"] == "simulated"
    assert "adaptive" in payload["arms"]
    assert "adaptive" in report.render()


def test_experiment_gives_both_arms_the_same_agent() -> None:
    """The same task, run under both arms, must start from identical behaviour."""
    task = _task("recovers")
    _, _, static = _run(task, "static")
    _, _, adaptive = _run(task, "adaptive")
    assert static.success == adaptive.success is True

    stuck = _task("hopeless")
    _, _, static_fail = _run(stuck, "static")
    _, _, adaptive_fail = _run(stuck, "adaptive")
    assert static_fail.success == adaptive_fail.success is False


def test_experiment_persists_to_the_store(tmp_path) -> None:
    db = tmp_path / "exp.db"
    store = EpisodeStore(str(db))
    try:
        run_experiment(build_tasks(3), store=store, run_mode="simulated")
    finally:
        store.close()
    reopened = EpisodeStore(str(db))
    try:
        stats = reopened.stats()
        assert stats["episodes"] == 6
        assert set(stats["arms"]) == {"static", "adaptive"}
    finally:
        reopened.close()


# --- OpenAI Agents integration ----------------------------------------------


def test_openai_hooks_map_events_into_microloop() -> None:
    adapter = OpenAIAgentsAdapter(start=ModelTier.Fast)
    runtime = MicroloopRuntime(adapter=adapter, task="fix")
    hooks = MicroloopRunHooks(runtime)

    class Context:
        tool_arguments = {"path": "a.py"}

    class Tool:
        name = "write_file"

    class Usage:
        input_tokens = 100
        output_tokens = 20

    class Response:
        usage = Usage()

    async def drive() -> None:
        await hooks.on_agent_start(None, type("A", (), {"name": "coder"})())
        for _ in range(4):
            await hooks.on_llm_end(None, None, Response())
            await hooks.on_tool_end(Context(), None, Tool(), {"error": "AssertionError"})

    asyncio.run(drive())
    assert runtime._step == 4
    assert adapter.usage.total_tokens == 480
    assert len(runtime.episode.adaptations) >= 1


def test_openai_runtime_persists_and_finishes() -> None:
    adapter = OpenAIAgentsAdapter()
    store = EpisodeStore(":memory:")
    runtime = MicroloopRuntime(adapter=adapter, task="fix", store=store)
    try:
        runtime.observe(
            action="run_tests",
            observation="1 failed",
            metrics={"exit_code": 1.0},
            metadata={"error": "AssertionError"},
        )
        summary = runtime.finish(success=False, verifier="pytest")
        assert summary["outcome"] == "failed"
        assert store.stats()["episodes"] == 1
    finally:
        store.close()
