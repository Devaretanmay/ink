use microloop_core::{
    Event, InterventionAction, Monitor, MonitorConfig, Policy, PolicyConfig, ProgressState, Reason,
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

fn event(step: u64, failed: bool) -> Event {
    let mut event = Event::new(step, "shell", "output");
    if failed {
        event.metrics = Some(nums(&[("exit_code", 1.0)]));
        event.metadata = Some(text(&[("error", "assertion-location-message")]));
    }
    event
}

fn verified(step: u64, failures: u64) -> Event {
    let mut event = Event::new(step, "shell", "");
    event.metrics = Some(nums(&[("exit_code", 1.0), ("failures", failures as f64)]));
    // Distinct error signatures keep verifier events from tripping the
    // recurrent-error detector, so stagnation/regression detection stays isolated.
    event.metadata = Some(text(&[
        ("error", &format!("err-{step}")),
        ("verifier", "suite-A"),
        ("verification_id", &format!("test-{step}")),
    ]));
    event
}

fn new_monitor() -> Monitor {
    Monitor::new()
}

#[test]
fn identical_failure_recurs_but_successful_polling_only_warns() {
    for (failed, expected) in [
        (true, ProgressState::Stalled),
        (false, ProgressState::Warning),
    ] {
        let mut monitor = new_monitor();
        for step in 1..3 {
            assert_eq!(
                monitor.observe(event(step, failed)).unwrap().status,
                ProgressState::Healthy
            );
        }
        assert_eq!(monitor.observe(event(3, failed)).unwrap().status, expected);
    }
}

#[test]
fn normalized_repetition() {
    let mut monitor = new_monitor();
    for step in 1..=3 {
        let mut event = event(step, true);
        event.action = format!(
            "run_test /tmp/run_10{step}/test.py --id 550e8400-e29b-41d4-a716-44665544000{step}"
        );
        event.observation = format!("Error at 0x7f8a9b1c2d3{step} at 2026-09-26T12:00:0{step}Z");
        event.metadata = Some(text(&[("error", &format!("error-step-{step}"))]));
        let decision = monitor.observe(event).unwrap();
        if step < 3 {
            assert_eq!(decision.status, ProgressState::Healthy);
        } else {
            assert_eq!(decision.status, ProgressState::Stalled);
            assert_eq!(decision.evidence.len(), 1);
            assert_eq!(decision.evidence[0].reason, Reason::NormalizedRepetition);
            assert_eq!(decision.evidence[0].steps, vec![1, 2, 3]);
        }
    }

    let mut disabled = Monitor::with_config(MonitorConfig {
        normalization: false,
        ..Default::default()
    })
    .unwrap();
    for step in 1..=3 {
        let mut event = event(step, true);
        event.action = format!(
            "run_test /tmp/run_10{step}/test.py --id 550e8400-e29b-41d4-a716-44665544000{step}"
        );
        event.observation = format!("Error at 0x7f8a9b1c2d3{step} at 2026-09-26T12:00:0{step}Z");
        event.metadata = Some(text(&[("error", &format!("error-step-{step}"))]));
        assert_eq!(
            disabled.observe(event).unwrap().status,
            ProgressState::Healthy
        );
    }
}

#[test]
fn improving_verifier_overrides_repeated_error_and_output() {
    let mut monitor = new_monitor();
    for step in 1..9 {
        let decision = monitor.observe(verified(step, 10 - step)).unwrap();
        assert_eq!(decision.status, ProgressState::Healthy);
        assert_eq!(decision.verified_progress, step > 1);
    }
}

#[test]
fn recurrent_error_across_different_actions_and_successful_reads() {
    let mut monitor = new_monitor();
    for step in 1..=5 {
        let mut event = event(step, step % 2 == 1);
        event.action = format!("action-{step}");
        let decision = monitor.observe(event).unwrap();
        if step == 5 {
            assert_eq!(decision.status, ProgressState::Stalled);
            assert_eq!(decision.evidence[0].reason, Reason::RepeatedError);
        }
    }
}

#[test]
fn missing_evidence_does_not_invent_progress_or_failure() {
    let mut monitor = new_monitor();
    for step in 1..50 {
        let event = Event::new(step, "shell", "");
        let decision = monitor.observe(event).unwrap();
        assert_eq!(decision.status, ProgressState::Healthy);
        assert!(!decision.verified_progress);
    }
}

#[test]
fn stagnation_needs_fresh_same_scope_measurements() {
    let mut monitor = new_monitor();
    for step in [1, 5, 9] {
        let mut event = verified(step, 4);
        event.action = format!("action-{step}");
        let decision = monitor.observe(event).unwrap();
        if step == 9 {
            assert_eq!(decision.status, ProgressState::Stalled);
            assert_eq!(decision.evidence[0].reason, Reason::StateStagnation);
        }
    }

    let mut monitor = new_monitor();
    for step in [1, 5, 9, 13] {
        let mut event = verified(step, 4);
        event.metadata = Some(text(&[
            ("verifier", "suite-A"),
            ("verification_id", "cached"),
        ]));
        assert_eq!(
            monitor.observe(event).unwrap().status,
            ProgressState::Healthy
        );
    }
}

#[test]
fn regression_and_scope_switch_are_distinct() {
    let mut monitor = new_monitor();
    monitor.observe(verified(1, 7)).unwrap();
    monitor.observe(verified(2, 2)).unwrap();
    let decision = monitor.observe(verified(3, 6)).unwrap();
    assert_eq!(decision.status, ProgressState::Regressing);
    assert_eq!(decision.evidence[0].steps, vec![2, 3]);

    let mut other = verified(4, 20);
    other.metadata = Some(text(&[
        ("verifier", "suite-B"),
        ("verification_id", "test-4"),
    ]));
    assert_eq!(
        monitor.observe(other).unwrap().status,
        ProgressState::Healthy
    );
}

#[test]
fn state_oscillation_warns_without_claiming_goal_failure() {
    let mut monitor = new_monitor();
    for step in 1..=6 {
        let mut event = Event::new(step, format!("action-{step}"), "");
        event.state = Some(text(&[("state", &format!("state-{}", step % 2))]));
        let decision = monitor.observe(event).unwrap();
        if step == 6 {
            assert_eq!(decision.status, ProgressState::Warning);
            assert_eq!(decision.evidence[0].reason, Reason::StateOscillation);
        }
    }
}

#[test]
fn invalid_events_do_not_mutate_history() {
    let mut monitor = new_monitor();
    monitor.observe(event(1, true)).unwrap();
    assert!(monitor.observe(event(1, true)).is_err());
    assert!(monitor.observe(Event::new(2, "", "output")).is_err());
    assert_eq!(
        monitor.observe(event(2, true)).unwrap().status,
        ProgressState::Healthy
    );
    assert_eq!(
        new_monitor().observe(event(3, true)).unwrap().status,
        ProgressState::Healthy
    );
}

#[test]
fn conflicting_cached_verification_rejected_without_consuming_step() {
    let mut monitor = new_monitor();
    monitor.observe(verified(1, 4)).unwrap();

    let mut bad = verified(2, 5);
    bad.metadata = Some(text(&[
        ("verifier", "suite-A"),
        ("verification_id", "test-1"),
    ]));
    assert!(monitor.observe(bad).is_err());

    let mut fresh = verified(2, 3);
    fresh.metadata = Some(text(&[
        ("verifier", "suite-A"),
        ("verification_id", "test-2"),
    ]));
    assert!(monitor.observe(fresh).unwrap().verified_progress);
}

#[test]
fn old_errors_expire_outside_window() {
    let mut monitor = Monitor::with_config(MonitorConfig {
        window: 4,
        ..Default::default()
    })
    .unwrap();
    monitor.observe(event(1, true)).unwrap();
    monitor.observe(event(2, true)).unwrap();
    for step in 3..10 {
        let mut event = event(step, false);
        event.action = step.to_string();
        monitor.observe(event).unwrap();
    }
    assert_eq!(
        monitor.observe(event(10, true)).unwrap().status,
        ProgressState::Healthy
    );
}

#[test]
fn policy_defaults_observe_and_opt_in_has_cooldown_and_cap() {
    let mut monitor = new_monitor();
    let mut active = Policy::new(PolicyConfig {
        stalled: InterventionAction::Replan,
        regressing: InterventionAction::Stop,
        cooldown_steps: 3,
        max_interventions: 2,
        stop_at_step: Some(10),
        ..Default::default()
    })
    .unwrap();
    let mut interventions = Vec::new();
    for step in 1..=10 {
        let decision = monitor.observe(event(step, true)).unwrap();
        assert_eq!(
            Policy::default().evaluate(&decision),
            InterventionAction::Observe
        );
        let action = active.evaluate(&decision);
        if action == InterventionAction::Replan {
            interventions.push(step);
        }
        if step == 10 {
            assert_eq!(action, InterventionAction::Stop);
        }
    }
    assert_eq!(interventions, vec![3, 6]);
}

#[test]
fn config_and_schema_are_strict() {
    assert!(Monitor::with_config(MonitorConfig {
        repetitions: 0,
        ..Default::default()
    })
    .is_err());
    assert!(serde_json::from_str::<MonitorConfig>(r#"{"windwo":4}"#).is_err());
    assert!(Policy::new(PolicyConfig {
        cooldown_steps: 0,
        ..Default::default()
    })
    .is_err());
    assert!(new_monitor().observe(Event::new(1, "", "out")).is_err());
}
