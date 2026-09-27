use microloop_core::{
    Budget, Capabilities, CapabilityLevel, ControllerConfig, Event, InterventionAction, Monitor,
    MonitorConfig, Policy, PolicyConfig, ProgressState, RecommendationReason, RuntimeAction,
    RuntimeState,
};
use std::collections::BTreeMap;

fn text(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
    pairs
        .iter()
        .map(|(key, value)| (key.to_string(), value.to_string()))
        .collect()
}

fn nums(pairs: &[(&str, f64)]) -> BTreeMap<String, f64> {
    pairs
        .iter()
        .map(|(key, value)| (key.to_string(), *value))
        .collect()
}

fn failing(step: u64) -> Event {
    let mut event = Event::new(step, "pytest tests/", "1 failed, 4 passed");
    event.metrics = Some(nums(&[("exit_code", 1.0)]));
    event.metadata = Some(text(&[("error", "AssertionError: test_admin.py:42")]));
    event.runtime = Some(RuntimeState {
        model: Some("sonnet".into()),
        context_tokens: Some(42_000),
        context_limit: Some(64_000),
        cost: Some(0.82),
        ..Default::default()
    });
    event
}

fn opt_in_policy() -> Policy {
    Policy::new(PolicyConfig {
        stalled: InterventionAction::Replan,
        regressing: InterventionAction::Stop,
        cooldown_steps: 1,
        max_interventions: 5,
        ..Default::default()
    })
    .unwrap()
}

#[test]
fn pass_one_exit_criteria() {
    let mut monitor = Monitor::with_runtime(
        MonitorConfig::default(),
        opt_in_policy(),
        Capabilities::default(),
        Budget::default(),
    )
    .unwrap();

    for step in 1..3 {
        let decision = monitor.observe(failing(step)).unwrap();
        assert_eq!(decision.status, ProgressState::Healthy);
    }
    let decision = monitor.observe(failing(3)).unwrap();

    assert_eq!(decision.progress.state, ProgressState::Stalled);
    assert_eq!(decision.runtime.model.as_deref(), Some("sonnet"));
    assert_eq!(decision.recommendation.action, RuntimeAction::Replan);
    assert_eq!(
        decision.recommendation.reason,
        RecommendationReason::TrajectoryStalled
    );
    // Legacy fields stay in sync.
    assert_eq!(decision.status, ProgressState::Stalled);
    assert_eq!(decision.intervention, InterventionAction::Replan);
}

#[test]
fn default_monitor_only_recommends_continue() {
    let mut monitor = Monitor::new();
    for step in 1..=3 {
        let decision = monitor.observe(failing(step)).unwrap();
        if step == 3 {
            assert_eq!(decision.progress.state, ProgressState::Stalled);
            assert_eq!(decision.recommendation.action, RuntimeAction::Continue);
            assert_eq!(
                decision.recommendation.reason,
                RecommendationReason::ObservationOnly
            );
            assert_eq!(decision.intervention, InterventionAction::Observe);
        }
    }
}

#[test]
fn exhausted_budget_stops_even_when_observing_only() {
    let mut monitor = Monitor::with_runtime(
        MonitorConfig::default(),
        Policy::default(),
        Capabilities::default(),
        Budget {
            max_cost: Some(1.0),
            ..Default::default()
        },
    )
    .unwrap();
    let mut event = Event::new(1, "run", "out");
    event.runtime = Some(RuntimeState {
        cost: Some(1.25),
        ..Default::default()
    });
    let decision = monitor.observe(event).unwrap();
    assert_eq!(decision.recommendation.action, RuntimeAction::Stop);
    assert_eq!(
        decision.recommendation.reason,
        RecommendationReason::BudgetExhausted
    );
}

#[test]
fn unsupported_action_degrades_to_continue() {
    let mut monitor = Monitor::with_runtime(
        MonitorConfig::default(),
        opt_in_policy(),
        Capabilities {
            replan: false,
            ..Default::default()
        },
        Budget::default(),
    )
    .unwrap();
    for step in 1..=3 {
        let decision = monitor.observe(failing(step)).unwrap();
        if step == 3 {
            assert_eq!(decision.recommendation.action, RuntimeAction::Continue);
            assert_eq!(
                decision.recommendation.reason,
                RecommendationReason::ActionUnsupported
            );
        }
    }
}

#[test]
fn runtime_state_is_optional_and_backwards_compatible() {
    // An event with no runtime field still produces a full decision.
    let mut monitor = Monitor::new();
    let decision = monitor.observe(Event::new(1, "shell", "out")).unwrap();
    assert_eq!(decision.progress.state, ProgressState::Healthy);
    assert!(decision.runtime.is_empty());
    assert_eq!(decision.recommendation.action, RuntimeAction::Continue);

    assert_eq!(
        Event::new(1, "shell", "out").capability_level(),
        CapabilityLevel::Signals
    );
    assert_eq!(failing(1).capability_level(), CapabilityLevel::Runtime);
}

#[test]
fn derived_remaining_budget_is_filled_from_the_configured_budget() {
    let mut monitor = Monitor::with_runtime(
        MonitorConfig::default(),
        Policy::default(),
        Capabilities::default(),
        Budget {
            max_cost: Some(2.0),
            ..Default::default()
        },
    )
    .unwrap();
    let decision = monitor.observe(failing(1)).unwrap();
    assert_eq!(decision.runtime.remaining_budget, Some(2.0 - 0.82));
}

fn adaptive() -> ControllerConfig {
    ControllerConfig {
        stalled: RuntimeAction::Replan,
        cooldown_steps: 1,
        max_interventions: 10,
        escalate_cooldown: 1,
        ..Default::default()
    }
}

#[test]
fn context_pressure_compacts_through_the_monitor() {
    let mut monitor = Monitor::from_controller_config(
        MonitorConfig::default(),
        adaptive(),
        Capabilities {
            context_compaction: true,
            ..Default::default()
        },
        Budget::default(),
    )
    .unwrap();

    let mut last = None;
    for step in 1..=3 {
        let mut event = failing(step);
        if let Some(runtime) = event.runtime.as_mut() {
            runtime.context_tokens = Some(60_000);
        }
        last = Some(monitor.observe(event).unwrap());
    }
    let decision = last.unwrap();
    assert_eq!(
        decision.recommendation.action,
        RuntimeAction::CompactContext
    );
    assert_eq!(
        decision.recommendation.reason,
        RecommendationReason::ContextPressure
    );
}

#[test]
fn stalled_run_replans_then_escalates() {
    let mut monitor = Monitor::from_controller_config(
        MonitorConfig::default(),
        adaptive(),
        Capabilities {
            model_switch: true,
            ..Default::default()
        },
        Budget::default(),
    )
    .unwrap();
    for step in 1..=3 {
        let _ = monitor.observe(failing(step)).unwrap();
    }
    let escalated = monitor.observe(failing(10)).unwrap();
    assert_eq!(
        escalated.recommendation.action,
        RuntimeAction::EscalateModel
    );
    assert_eq!(
        escalated.recommendation.reason,
        RecommendationReason::TrajectoryStalled
    );
}

#[test]
fn sustained_progress_deescalates_after_an_escalation() {
    let mut config = adaptive();
    config.deescalate_after = 1;
    let mut monitor = Monitor::from_controller_config(
        MonitorConfig::default(),
        config,
        Capabilities {
            model_switch: true,
            ..Default::default()
        },
        Budget::default(),
    )
    .unwrap();
    for step in 1..=3 {
        let _ = monitor.observe(failing(step)).unwrap();
    }
    let escalated = monitor.observe(failing(10)).unwrap();
    assert_eq!(
        escalated.recommendation.action,
        RuntimeAction::EscalateModel
    );

    let decision = monitor
        .observe(Event::new(11, "read src/auth.rs", "contents"))
        .unwrap();
    assert_eq!(decision.progress.state, ProgressState::Healthy);
    assert_eq!(
        decision.recommendation.action,
        RuntimeAction::DeescalateModel
    );
    assert_eq!(
        decision.recommendation.reason,
        RecommendationReason::ProgressRecovered
    );
}

#[test]
fn availability_blocks_a_capable_actuator_but_not_stop() {
    let mut monitor = Monitor::from_controller_config(
        MonitorConfig::default(),
        adaptive(),
        Capabilities::default(),
        Budget::default(),
    )
    .unwrap();
    let mut last = None;
    for step in 1..=3 {
        last = Some(
            monitor
                .observe_with_available(failing(step), Some(Vec::new()))
                .unwrap(),
        );
    }
    let decision = last.unwrap();
    assert_eq!(decision.recommendation.action, RuntimeAction::Continue);
    assert_eq!(
        decision.recommendation.reason,
        RecommendationReason::ActionUnsupported
    );

    let mut stopping = Monitor::from_controller_config(
        MonitorConfig::default(),
        ControllerConfig::default(),
        Capabilities::default(),
        Budget {
            max_cost: Some(1.0),
            ..Default::default()
        },
    )
    .unwrap();
    let mut event = Event::new(1, "run", "out");
    event.runtime = Some(RuntimeState {
        cost: Some(2.0),
        ..Default::default()
    });
    let decision = stopping
        .observe_with_available(event, Some(Vec::new()))
        .unwrap();
    assert_eq!(decision.recommendation.action, RuntimeAction::Stop);
}
