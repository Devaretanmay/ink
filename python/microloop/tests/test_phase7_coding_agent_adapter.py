from __future__ import annotations

from pathlib import Path

import pytest
from microloop import Microloop, Outcome, PromotionRequirements
from microloop.internal.verification import statistics

from integrations.coding_agent import (
    ALLOWED_RECOVERY_CHOICES,
    CODING_AGENT_STATE_SCHEMA,
    AgentEvent,
    CodingAgentRecoveryAdapter,
    ContextPatch,
    LocalRetrievalAdapter,
    TrajectoryWindow,
    evaluate_progress_resumed,
    extract_trajectory_features,
)
from integrations.coding_agent.harness import (
    run_baseline_comparison,
    run_observe_and_shadow_pilot,
)

REQ = PromotionRequirements(10, 0.5, 0.5, 0.6, 0.25, 5, 100)


def test_event_normalization_and_trajectory_window() -> None:
    ev = AgentEvent("file_read", path="foo.py")
    assert ev.event_type == "file_read"
    assert ev.path == "foo.py"

    with pytest.raises(ValueError, match="Unknown agent event_type"):
        AgentEvent("invalid_action_type")

    window = TrajectoryWindow(max_size=5)
    for i in range(10):
        window.append(AgentEvent("command_run", command=f"cmd_{i}"))
    assert len(window) == 5
    events = window.events()
    assert events[0].command == "cmd_5"
    assert events[-1].command == "cmd_9"


def test_trajectory_features_deterministic() -> None:
    events = [
        AgentEvent("file_read", path="app/main.py"),
        AgentEvent("file_search", query="timeout_setting"),
        AgentEvent("file_search", query="timeout_setting"),
        AgentEvent("file_edit", path="app/main.py"),
        AgentEvent("command_failed", error="AssertionError: timeout != 30"),
        AgentEvent("command_failed", error="AssertionError: timeout != 30"),
        AgentEvent("test_failed", test_counts={"passed": 2, "failed": 1}),
    ]
    f1 = extract_trajectory_features(events)
    f2 = extract_trajectory_features(events)
    assert f1 == f2
    assert f1["current_error_signature"] == "AssertionError"
    assert f1["same_error_count"] == 2
    assert f1["failed_command_streak"] == 2
    assert f1["test_failure_streak"] == 1
    assert f1["repeated_search_count"] == 1
    assert f1["search_query_repeat_count"] == 2
    assert f1["unique_files_read"] == 1
    assert f1["unique_files_edited"] == 1


def test_feature_sensitivity_to_repetition_stagnation_regression() -> None:
    events = [
        AgentEvent("file_read", path="a.py"),
        AgentEvent("file_read", path="a.py"),
        AgentEvent("file_edit", path="b.py"),
        AgentEvent("file_edit", path="b.py"),
        AgentEvent("file_edit", path="b.py"),
        AgentEvent("edit_reverted", path="b.py"),
        AgentEvent("test_failed", test_counts={"passed": 5, "failed": 2}),
        AgentEvent("test_failed", test_counts={"passed": 4, "failed": 3}),
    ]
    feats = extract_trajectory_features(events)
    assert feats["repeated_file_read_count"] == 1
    assert feats["same_file_edit_count"] == 3
    assert feats["edit_revert_count"] == 1
    assert feats["test_failure_streak"] == 2
    assert feats["recent_test_delta"] == 1


def test_decision_site_registration_and_contract(tmp_path: Path) -> None:
    with Microloop(tmp_path / "test.db") as ml:
        adapter = CodingAgentRecoveryAdapter(ml, repo_path=tmp_path)
        assert adapter.site.name == "coding_agent.recovery_action"
        assert set(adapter.site.choices) == set(ALLOWED_RECOVERY_CHOICES)
        for field_name, field_type in CODING_AGENT_STATE_SCHEMA.items():
            assert adapter.site.state_schema[field_name] == field_type


def test_recovery_action_fallback_and_non_destructive(tmp_path: Path) -> None:
    with Microloop(tmp_path / "test.db") as ml:
        adapter = CodingAgentRecoveryAdapter(ml, repo_path=tmp_path)
        adapter.record_event(AgentEvent("file_search", query="q"))
        adapter.record_event(AgentEvent("file_search", query="q"))
        adapter.record_event(AgentEvent("command_failed", error="TypeError: NoneType"))
        adapter.record_event(AgentEvent("command_failed", error="TypeError: NoneType"))

        decision = adapter.decide()
        assert decision.choice in ALLOWED_RECOVERY_CHOICES
        assert decision.choice in ("retrieve_context", "replan")

        with pytest.raises(ValueError, match="Disallowed recovery action"):
            adapter.decide(fallback_fn=lambda: "delete_files")


def test_context_patch_construction_and_local_sources(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "worker.py").write_text("def handle_job():\n    pass\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_worker.py").write_text("def test_worker():\n    assert True\n")

    retriever = LocalRetrievalAdapter(tmp_path)
    patch = retriever.retrieve(
        question="Where is handle_job defined?",
        query="handle_job",
        reason="test_patch",
        max_tokens=1000,
    )
    assert isinstance(patch, ContextPatch)
    assert patch.reason == "test_patch"
    assert patch.token_count > 0
    assert any(e["path"].endswith("worker.py") for e in patch.evidence)


def test_outcome_scoring_and_unknown_handling() -> None:
    before = [
        AgentEvent("command_failed", error="AssertionError: 1 != 2"),
        AgentEvent("command_failed", error="AssertionError: 1 != 2"),
    ]

    after_pass = [
        AgentEvent("file_read", path="lib/calc.py"),
        AgentEvent("test_passed", test_counts={"passed": 1, "failed": 0}),
    ]
    score_p, reason_p, _ = evaluate_progress_resumed(before, after_pass)
    assert score_p == 1.0
    assert "test_passed" in reason_p

    after_loop = [
        AgentEvent("command_failed", error="AssertionError: 1 != 2"),
        AgentEvent("command_failed", error="AssertionError: 1 != 2"),
    ]
    score_f, reason_f, _ = evaluate_progress_resumed(before, after_loop)
    assert score_f == 0.0
    assert "same_error_loop_persists" in reason_f

    after_empty: list[AgentEvent] = []
    score_u, reason_u, _ = evaluate_progress_resumed(before, after_empty)
    assert score_u is None
    assert "insufficient_after_events" in reason_u

    records = [
        {"task": "t1", "candidate": {"quality": 1.0}, "baseline": {"quality": 1.0}},
        {"task": "t2", "candidate": {"quality": None}, "baseline": {"quality": 1.0}},
    ]
    stats = statistics(records)
    assert stats["observed_positive"] == 1
    assert stats["observed_negative"] == 0
    assert stats["unknown"] == 1
    assert stats["quality"] == 1.0


def test_observe_stage_data_collection(tmp_path: Path) -> None:
    report = run_observe_and_shadow_pilot(tmp_path, tmp_path / "pilot.db", episodes_per_task=2)
    assert report.total_decisions == 10
    assert report.repetition_rate > 0.0
    assert report.known_outcomes == 10
    assert report.unknown_outcomes == 0
    assert report.recovery_success_rate == 1.0


def test_shadow_stage_candidate_generation(tmp_path: Path) -> None:
    with Microloop(tmp_path / "shadow.db") as ml:
        adapter = CodingAgentRecoveryAdapter(ml, repo_path=tmp_path)
        for i in range(25):
            adapter.window.clear()
            adapter.record_event(AgentEvent("file_search", query="auth"))
            adapter.record_event(AgentEvent("file_search", query="auth"))
            dec = adapter.decide(task_id=f"train_{i}")
            before = list(adapter.window.events())
            after = [AgentEvent("test_passed")]
            adapter.record_outcome(dec.decision_id, before, after)

        ml.compile(adapter.site, engine="exact")
        art = ml._artifact(adapter.site.version)
        assert art is not None
        assert art["status"] == "SHADOW"

        adapter.window.clear()
        adapter.record_event(AgentEvent("file_search", query="auth"))
        adapter.record_event(AgentEvent("file_search", query="auth"))
        eval_dec = adapter.decide(task_id="eval_shadow")
        assert eval_dec.source == "fallback"
        assert eval_dec.fallback_reason == "shadow"


def test_controlled_active_and_comparison_traffic(tmp_path: Path) -> None:
    def verify_fn(state: dict, choice: str) -> Outcome:
        expected = "retrieve_context" if state.get("repeated_search_count", 0) >= 2 else "continue"
        ev = {"expected": expected}
        return Outcome(float(choice == expected), "coding_agent_verifier", "1", ev)

    with Microloop(tmp_path / "active.db") as ml:
        adapter = CodingAgentRecoveryAdapter(ml, repo_path=tmp_path)

        for i in range(250):
            adapter.window.clear()
            adapter.record_event(AgentEvent("file_search", query="auth"))
            adapter.record_event(AgentEvent("file_search", query="auth"))
            dec = adapter.decide(task_id=f"obs_{i}")
            out = verify_fn(dec.state, dec.choice)
            ml.record_outcome(
                dec.decision_id,
                quality=out.quality,
                verifier=out.verifier,
                verifier_version=out.verifier_version,
                evidence=out.evidence,
            )

        ml.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")

        for i in range(100):
            adapter.window.clear()
            adapter.record_event(AgentEvent("file_search", query="auth"))
            adapter.record_event(AgentEvent("file_search", query="auth"))
            dec = adapter.decide(task_id=f"shad_{i}")
            out = verify_fn(dec.state, dec.choice)
            ml.record_outcome(
                dec.decision_id,
                quality=out.quality,
                verifier=out.verifier,
                verifier_version=out.verifier_version,
                evidence=out.evidence,
            )

        ml.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")

        art = ml._artifact(adapter.site.version)
        assert art["status"] == "ACTIVE"

        sources = set()
        for _ in range(40):
            adapter.window.clear()
            adapter.record_event(AgentEvent("file_search", query="auth"))
            adapter.record_event(AgentEvent("file_search", query="auth"))
            res = adapter.decide()
            sources.add(res.source)
        assert "fast_path" in sources


def test_drift_demotion_in_coding_agent_workload(tmp_path: Path) -> None:
    def verify_fn(state: dict, choice: str) -> Outcome:
        expected = "retrieve_context" if state.get("repeated_search_count", 0) >= 2 else "continue"
        ev = {"expected": expected}
        return Outcome(float(choice == expected), "coding_agent_verifier", "1", ev)

    with Microloop(tmp_path / "drift.db") as ml:
        adapter = CodingAgentRecoveryAdapter(ml, repo_path=tmp_path)

        for i in range(250):
            adapter.window.clear()
            adapter.record_event(AgentEvent("file_search", query="auth"))
            adapter.record_event(AgentEvent("file_search", query="auth"))
            dec = adapter.decide(task_id=f"obs_{i}")
            out = verify_fn(dec.state, dec.choice)
            ml.record_outcome(
                dec.decision_id,
                quality=out.quality,
                verifier=out.verifier,
                verifier_version=out.verifier_version,
                evidence=out.evidence,
            )

        ml.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")

        for i in range(100):
            adapter.window.clear()
            adapter.record_event(AgentEvent("file_search", query="auth"))
            adapter.record_event(AgentEvent("file_search", query="auth"))
            dec = adapter.decide(task_id=f"shad_{i}")
            out = verify_fn(dec.state, dec.choice)
            ml.record_outcome(
                dec.decision_id,
                quality=out.quality,
                verifier=out.verifier,
                verifier_version=out.verifier_version,
                evidence=out.evidence,
            )

        ml.maintenance(verifier=verify_fn, requirements=REQ, engine="exact")
        assert ml._artifact(adapter.site.version)["status"] == "ACTIVE"

        drift_dec = adapter.decide(task_id="drift_1")
        ml.record_outcome(
            drift_dec.decision_id,
            quality=0.0,
            verifier="changed_verifier_v2",
            verifier_version="2",
            evidence={},
        )
        res = ml.reevaluate(adapter.site)
        assert res.get("demoted") is True
        assert ml._artifact(adapter.site.version)["status"] == "SHADOW"


def test_baseline_experiment_agent_alone_vs_static_vs_reactive(tmp_path: Path) -> None:
    results = run_baseline_comparison(tmp_path, tmp_path / "compare.db")

    alone = results["A_agent_alone"]
    static = results["B_static_retrieval"]
    reactive = results["C_microloop_reactive"]

    assert len(alone) == 5
    assert len(static) == 5
    assert len(reactive) == 5

    assert sum(1 for r in alone if r.completed) == 0
    assert sum(1 for r in static if r.completed) == 2
    assert sum(1 for r in reactive if r.completed) == 5

    avg_tokens_static = sum(r.total_tokens for r in static) / len(static)
    avg_tokens_reactive = sum(r.total_tokens for r in reactive) / len(reactive)
    assert avg_tokens_reactive < avg_tokens_static

    avg_cost_static = sum(r.cost_usd for r in static) / len(static)
    avg_cost_reactive = sum(r.cost_usd for r in reactive) / len(reactive)
    assert avg_cost_reactive < avg_cost_static
