import microloop
from microloop import InterventionAction, Monitor, Policy


def test_public_surface_stays_small() -> None:
    """Guard against the API silently regrowing between releases."""
    assert microloop.__all__ == [
        "ActionOutcome",
        "ActionScore",
        "AdaptationRecord",
        "AdaptationResult",
        "AgentRuntimeAdapter",
        "ApplyResult",
        "Budget",
        "Capabilities",
        "CapabilityLevel",
        "ContextCompactor",
        "ControllerTrace",
        "Decision",
        "Episode",
        "Event",
        "InterventionAction",
        "MockAdapter",
        "ModelTier",
        "Monitor",
        "Policy",
        "ProgressSnapshot",
        "ProgressState",
        "RecommendationReason",
        "RuntimeAction",
        "RuntimeAdapter",
        "RuntimeController",
        "RuntimeDecision",
        "RuntimeSession",
        "RuntimeState",
        "SCHEMA_VERSION",
        "ScoredController",
        "Strategy",
        "TaskOutcome",
        "TieredAdapter",
        "Usage",
        "__version__",
        "Microloop", "DecisionSite", "DecisionResult", "FallbackResult",
        "Outcome", "PromotionRequirements", "decision", "record_outcome",
    ]
    assert not hasattr(microloop, "wrap")


def _failing(monitor: Monitor, step: int):
    return monitor.observe(
        action="pytest tests/",
        observation="1 failed, 4 passed",
        metrics={"exit_code": 1.0},
        metadata={"error": "AssertionError: test_admin.py:42"},
        step=step,
    )


def test_repeated_failure_is_stalled() -> None:
    monitor = Monitor()
    for step in range(1, 3):
        assert _failing(monitor, step).status == "healthy"
    decision = _failing(monitor, 3)
    assert decision.status == "stalled"
    assert "repeated_error" in decision.reasons


def test_default_policy_is_observation_only() -> None:
    monitor = Monitor()
    decision = _failing(monitor, 3)
    assert decision.intervention == InterventionAction.Observe
    assert not decision.should_intervene


def test_opt_in_policy_replans_with_recovery_context() -> None:
    policy = Policy(
        stalled=InterventionAction.Replan,
        cooldown_steps=1,
        max_interventions=5,
    )
    monitor = Monitor(policy=policy)
    for step in range(1, 4):
        decision = _failing(monitor, step)
    assert decision.intervention == InterventionAction.Replan
    assert decision.should_intervene
    assert "RECOVERY" in decision.recovery_context


def test_improving_verifier_is_healthy() -> None:
    monitor = Monitor()
    for step, failures in zip(range(1, 5), [8.0, 6.0, 4.0, 2.0], strict=True):
        decision = monitor.observe(
            action="pytest tests/",
            observation="running suite",
            metrics={"exit_code": 1.0, "failures": failures},
            metadata={
                "error": f"err-{step}",
                "verifier": "pytest",
                "verification_id": f"run-{step}",
            },
            step=step,
        )
        assert decision.status == "healthy"
        if step > 1:
            assert decision.verified_progress
