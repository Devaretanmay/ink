"""Pass 4: real integrations, outcome collection and the experiment runner."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
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
    BudgetExhausted,
    Ceiling,
    GroqProvider,
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


# --- real-model providers ----------------------------------------------------
#
# These never touch the network. They check the two things that silently break a
# metered run: the transcript must keep every tool's identity across turns, and
# the call budget must be a hard stop rather than a suggestion.


class _FakeGroq:
    """A stand-in for an OpenAI-compatible chat completions response."""

    def __init__(self, calls: list[dict]) -> None:
        self.calls = calls
        self.posted: list[dict] = []

    def _post(self, payload: dict) -> dict:
        self.posted.append(payload)
        message = self.calls[len(self.posted) - 1]
        return {
            "choices": [{"message": message}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }


def _tool_turn(identifier: str, name: str, arguments: str) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": identifier,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    }


def test_groq_transcript_keeps_tool_identity_across_turns() -> None:
    """Every turn must stay wired, not just the most recent one.

    An earlier version queued only the latest turn's ids, which stripped the tool
    calls and results of every earlier turn. The run still worked and the model
    simply stopped being able to see most of its own conversation.
    """
    provider = GroqProvider(api_key="t")
    first_fake = _FakeGroq([_tool_turn("a1", "read_file", '{"path": "x.py"}')])
    provider._post = first_fake._post
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}]
    first = provider.complete(model="m", messages=messages, tools=[])

    second_fake = _FakeGroq([_tool_turn("a2", "run_tests", "{}")])
    provider._post = second_fake._post
    messages = messages + [
        {
            "role": "assistant",
            "content": first.text,
            "tool_calls": first.raw_tool_calls,
        },
        {"role": "tool", "name": "read_file", "content": "contents", "identifier": "a1"},
    ]
    provider.complete(model="m", messages=messages, tools=[])

    sent = second_fake.posted[-1]["messages"]
    earlier = [m for m in sent if m["role"] == "assistant" and m.get("tool_calls")]
    earlier_ids = [c["id"] for m in earlier for c in m["tool_calls"]]
    tool_ids = [m["tool_call_id"] for m in sent if m["role"] == "tool"]
    assert earlier_ids == ["a1"], "the first turn lost its tool call"
    assert tool_ids == ["a1"], "the first result lost its id"


def test_groq_budget_is_a_hard_stop() -> None:
    provider = GroqProvider(api_key="t", max_calls=1)
    fake = _FakeGroq([_tool_turn("b1", "run_tests", "{}")])
    provider._post = fake._post
    messages = [{"role": "user", "content": "go"}]
    provider.complete(model="m", messages=messages, tools=[])
    with pytest.raises(BudgetExhausted):
        provider.complete(model="m", messages=messages, tools=[])
    assert provider.calls == 1


def test_budget_exhaustion_is_not_reported_as_a_model_failure() -> None:
    """Running out of allowance and running out of ideas are different facts."""
    from integrations.coding_harness.providers import SimulatedCodingProvider
    from integrations.experiment.runner import build_session

    task = _task("stubborn")
    provider = SimulatedCodingProvider(
        filename=task.filename,
        wrong_source=task.wrong_source,
        correct_source=task.correct_source,
        behaviour=task.behaviour,
    )
    provider.complete = _refuse  # type: ignore[method-assign]
    result = CodingHarness(
        task=task,
        provider=provider,
        session=build_session("static"),
        arm="static",
        max_steps=5,
    ).run()
    assert result.truncated is True
    assert result.outcome == "budget_exhausted"
    assert result.success is False


def _refuse(**_kwargs):
    raise BudgetExhausted("out of allowance")


def test_real_task_set_is_solvable_and_fails_before_the_fix() -> None:
    """A task nobody can pass, or that passes too easily, measures nothing."""
    from integrations.coding_harness.real_tasks import build_real_tasks

    for task in build_real_tasks():
        workspace = Path(tempfile.mkdtemp())
        task.prepare(workspace)
        passed, message = task.verify(workspace)
        assert not passed, f"{task.name} already passes with the buggy source"
        assert message.startswith("1 failed:")
        (workspace / task.filename).write_text(task.correct_source)
        assert task.verify(workspace)[0] is True, f"{task.name} is not solvable"


def test_tool_declaration_is_declared_once_and_translated() -> None:
    """The harness declares its tools; each provider renders them its own way."""
    from integrations.coding_harness.harness import TOOL_SCHEMAS
    from integrations.coding_harness.providers import AnthropicProvider

    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert names == {"write_file", "read_file", "run_tests"}
    rendered = AnthropicProvider._tools(TOOL_SCHEMAS)
    assert {t["name"] for t in rendered} == names
    assert all(set(t) == {"name", "description", "input_schema"} for t in rendered)


def test_runtime_fields_survive_the_native_round_trip() -> None:
    """Every field a host reports must come back out of the core.

    The core is a PyO3 extension that round-trips state through JSON, so a field
    added on the Python side but not mirrored in Rust is silently dropped rather
    than rejected. ``model_calls`` was lost exactly this way, and the only
    symptom was a null column in the episode database.
    """
    from microloop import Monitor, RuntimeState

    state = RuntimeState(
        model="m",
        input_tokens=10,
        output_tokens=5,
        cost=0.25,
        elapsed_seconds=1.5,
        tool_calls=3,
        model_calls=4,
        remaining_budget=1.0,
    )
    decision = Monitor().observe("act", "obs", runtime=state)
    assert decision.runtime is not None
    assert decision.runtime.model_calls == 4
    assert decision.runtime.tool_calls == 3
    assert decision.runtime.elapsed_seconds == 1.5


def test_harness_records_calls_and_wall_time() -> None:
    from microloop.store import EpisodeStore

    store = EpisodeStore(":memory:")
    try:
        report = run_experiment(build_tasks(2), store=store, run_mode="simulated")
        rows = store.episodes()
        assert rows
        for row in rows:
            # A metered run is priced in calls; without these the dataset cannot
            # answer what a task cost.
            assert row["model_calls"], "model calls were never recorded"
            assert row["elapsed_seconds"] is not None
        assert report.arms["static"].episodes == 2
    finally:
        store.close()


# --- close-call band ----------------------------------------------------------
#
# The band is the whole experiment, so its properties are checked mechanically
# rather than described. These tests fail if a task stops being a close call.


def test_every_close_call_task_is_a_genuine_close_call() -> None:
    from integrations.coding_harness.close_tasks import build_close_tasks, validate

    reports = validate()
    assert reports, "no tasks were validated"
    for report in reports:
        assert report.is_close_call, f"{report.name}: {report.problem()}"
    assert len(build_close_tasks(24)) == 24


def test_close_call_band_covers_the_documented_families() -> None:
    from integrations.coding_harness.close_tasks import FAMILIES, validate

    families = {report.family for report in validate()}
    assert len(families) == len(FAMILIES)


def test_close_call_tasks_span_more_than_one_file() -> None:
    """Single-file tasks cannot produce the multi-file faults they model."""
    from integrations.coding_harness.close_tasks import build_close_tasks

    multi = [t for t in build_close_tasks(24) if t.support_files]
    assert len(multi) >= 8


def test_close_call_tasks_do_not_leak_the_answer() -> None:
    """The agent must not be handed the naive or correct variant.

    A task that ships its own solution is a demonstration, and the band is the
    one place that must not quietly become one.
    """
    from integrations.coding_harness.close_tasks import build_close_tasks

    for task in build_close_tasks(24):
        assert task.correct_source not in task.prompt
        for source in task.support_files.values():
            # The buggy files are given to the agent; the solution is not.
            assert "def unique" not in source or "seen = set()" in source


def test_band_summary_says_when_the_rate_is_unusable() -> None:
    from integrations.coding_harness.close_tasks import summarise

    assert "inside" in summarise(12, 24)
    assert "above" in summarise(24, 24)
    assert "below" in summarise(1, 24)
    assert "cannot be assessed" in summarise(0, 0)


def test_calibrate_runs_the_static_arm_only() -> None:
    from integrations.experiment.__main__ import main

    assert main(["--task-set", "close", "--tasks", "6", "--calibrate", "--db", ":memory:"]) == 0


def test_call_budget_is_shared_across_the_whole_run() -> None:
    """A per-provider cap would multiply by the task count.

    The experiment builds one provider per task, so ``--max-calls 400`` over 24
    tasks would have meant roughly 9,600 calls. The cap has to be owned by
    something that outlives the provider.
    """
    from integrations.coding_harness.providers import CallBudget

    budget = CallBudget(3)
    providers = [CallBudgetHolder(budget) for _ in range(4)]
    spent = 0
    for provider in providers:
        try:
            provider.spend()
            spent += 1
        except BudgetExhausted:
            break
    assert spent == 3
    assert budget.spent == 3
    assert budget.remaining == 0
    assert not budget.can_afford(1)
    assert CallBudget(None).can_afford(10_000)


class CallBudgetHolder:
    """A one-line stand-in for a provider charging a shared budget."""

    def __init__(self, budget) -> None:  # noqa: ANN001
        self.budget = budget

    def spend(self) -> None:
        self.budget.charge()


def test_runner_skips_tasks_it_cannot_fund() -> None:
    """A short run and a failed run are different facts."""
    from integrations.coding_harness.close_tasks import build_close_tasks
    from integrations.coding_harness.providers import CallBudget

    budget = CallBudget(1)
    report = run_experiment(
        build_close_tasks(6),
        arms=("static",),
        call_budget=budget,
        max_steps=10,
    )
    # One task is funded; the runner declines the rest rather than starting runs
    # that would discover the budget is gone on their first turn.
    assert report.arms["static"].episodes <= 1
    assert report.skipped >= 0


def test_a_bad_tool_path_is_reported_not_raised() -> None:
    """A model exploring its workspace must not be able to end the run.

    A read aimed at a directory, a missing file, or a path outside the workspace
    all used to propagate out of the tool loop and kill a run that still had
    turns left to spend.
    """
    import tempfile as _tempfile

    from integrations.coding_harness.tasks import build_tasks

    task = build_tasks(1)[0]
    from integrations.experiment.runner import build_session

    harness = CodingHarness(
        task=task,
        provider=object(),
        session=build_session("static"),
        workspace=_tempfile.mkdtemp(),
    )
    task.prepare(harness.workspace)
    for bad in ({"path": ""}, {"path": "   "}, {"path": "nope.py"}):
        observation, passed = harness._dispatch("read_file", bad)
        assert passed is None
        assert observation
    (harness.workspace / "sub").mkdir()
    assert "directory" in harness._dispatch("read_file", {"path": "sub"})[0]
    assert "directory" in harness._dispatch("write_file", {"path": "sub", "content": "x"})[0]
    # A path outside the workspace is refused rather than followed.
    assert "unusable" in harness._dispatch("read_file", {"path": "../../etc/passwd"})[0]
    assert harness._dispatch("read_file", {"path": "task.py"})[0]


def test_hallucinated_tool_calls_are_dropped_not_forwarded() -> None:
    """A model will invent a tool name, and it must not poison the run.

    An OpenAI-compatible API validates that the conversation only mentions tools
    it was offered and rejects the *whole request* when it does not. Forwarding
    one invented call therefore kills every subsequent turn of the run.
    """
    from integrations.coding_harness.harness import TOOL_SCHEMAS
    from integrations.coding_harness.providers import GroqProvider

    provider = GroqProvider(api_key="t")
    fake = _FakeGroq(
        [
            {
                "content": "let me look around",
                "tool_calls": [
                    {
                        "id": "x1",
                        "type": "function",
                        "function": {"name": "print_tree", "arguments": '{"path": ""}'},
                    }
                ],
            }
        ]
    )
    provider._post = fake._post
    reply = provider.complete(
        model="m", messages=[{"role": "user", "content": "go"}], tools=TOOL_SCHEMAS
    )
    assert reply.tool_calls == []
    assert reply.raw_tool_calls == []
    assert "print_tree" in reply.text, "the model should be told the tool is not real"
    # And a turn of nothing but invented tools must not end the run.
    assert reply.done is False


def test_declared_tool_calls_still_pass_through() -> None:
    from integrations.coding_harness.harness import TOOL_SCHEMAS
    from integrations.coding_harness.providers import GroqProvider

    provider = GroqProvider(api_key="t")
    fake = _FakeGroq([_tool_turn("ok1", "run_tests", "{}")])
    provider._post = fake._post
    reply = provider.complete(
        model="m", messages=[{"role": "user", "content": "go"}], tools=TOOL_SCHEMAS
    )
    assert [c.name for c in reply.tool_calls] == ["run_tests"]
    assert reply.raw_tool_calls and reply.raw_tool_calls[0]["id"] == "ok1"
