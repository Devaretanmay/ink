use microloop::monitor::*;
use microloop::policy::*;

fn event(step: u64, failed: bool) -> Event {
    Event {
        schema_version: 1,
        run_id: "test".into(),
        step,
        action: Action {
            name: "shell".into(),
            fingerprint: "command".into(),
            normalized_fingerprint: None,
        },
        observation: Observation {
            success: Some(!failed),
            fingerprint: Some("output".into()),
            error_fingerprint: failed.then(|| "assertion-location-message".into()),
            normalized_fingerprint: None,
        },
        verification: None,
        state_fingerprint: None,
    }
}
fn verified(step: u64, failures: u64) -> Event {
    let mut e = event(step, failures > 0);
    e.verification = Some(Verification {
        scope: "suite-A".into(),
        observation_id: format!("test-{step}"),
        failures,
    });
    e
}
fn monitor() -> Monitor {
    Monitor::new("test".into(), MonitorConfig::default()).unwrap()
}

#[test]
fn identical_failure_recurs_but_successful_polling_only_warns() {
    for (failed, expected) in [
        (true, ProgressState::Stalled),
        (false, ProgressState::Warning),
    ] {
        let mut m = monitor();
        for s in 1..3 {
            assert_eq!(
                m.observe(event(s, failed)).unwrap().state,
                ProgressState::Healthy
            );
        }
        assert_eq!(m.observe(event(3, failed)).unwrap().state, expected);
    }
}

#[test]
fn normalized_repetition() {
    let mut m = monitor();
    for s in 1..=3 {
        let mut e = event(s, true);
        e.action.fingerprint =
            format!("run_test /tmp/run_10{s}/test.py --id 550e8400-e29b-41d4-a716-44665544000{s}");
        e.observation.fingerprint = Some(format!(
            "Error at 0x7f8a9b1c2d3{s} at 2026-09-26T12:00:0{s}Z"
        ));
        e.observation.error_fingerprint = Some(format!("error-step-{s}"));
        let d = m.observe(e).unwrap();
        if s < 3 {
            assert_eq!(d.state, ProgressState::Healthy);
        } else {
            assert_eq!(d.state, ProgressState::Stalled);
            assert_eq!(d.evidence.len(), 1);
            assert_eq!(d.evidence[0].reason, Reason::NormalizedRepetition);
            assert_eq!(d.evidence[0].steps, vec![1, 2, 3]);
        }
    }

    let mut disabled_m = Monitor::new(
        "test".into(),
        MonitorConfig {
            normalize_actions: false,
            ..Default::default()
        },
    )
    .unwrap();
    for s in 1..=3 {
        let mut e = event(s, true);
        e.action.fingerprint =
            format!("run_test /tmp/run_10{s}/test.py --id 550e8400-e29b-41d4-a716-44665544000{s}");
        e.observation.fingerprint = Some(format!(
            "Error at 0x7f8a9b1c2d3{s} at 2026-09-26T12:00:0{s}Z"
        ));
        e.observation.error_fingerprint = Some(format!("error-step-{s}"));
        let d = disabled_m.observe(e).unwrap();
        assert_eq!(d.state, ProgressState::Healthy);
    }
}
#[test]
fn improving_verifier_overrides_repeated_error_and_output() {
    let mut m = monitor();
    for s in 1..9 {
        let d = m.observe(verified(s, 10 - s)).unwrap();
        assert_eq!(d.state, ProgressState::Healthy);
        assert_eq!(d.verified_progress, s > 1);
    }
}
#[test]
fn recurrent_error_across_different_actions_and_successful_reads() {
    let mut m = monitor();
    for s in 1..=5 {
        let mut e = event(s, s % 2 == 1);
        e.action.fingerprint = format!("action-{s}");
        let d = m.observe(e).unwrap();
        if s == 5 {
            assert_eq!(d.state, ProgressState::Stalled);
            assert_eq!(d.evidence[0].reason, Reason::RepeatedError);
        }
    }
}
#[test]
fn missing_evidence_does_not_invent_progress_or_failure() {
    let mut m = monitor();
    for s in 1..50 {
        let mut e = event(s, false);
        e.observation = Observation::default();
        let d = m.observe(e).unwrap();
        assert_eq!(d.state, ProgressState::Healthy);
        assert!(!d.verified_progress);
    }
}
#[test]
fn stagnation_needs_fresh_same_scope_measurements() {
    let mut m = monitor();
    for s in [1, 5, 9] {
        let mut e = verified(s, 4);
        e.action.fingerprint = format!("action-{s}");
        e.observation = Observation::default();
        let d = m.observe(e).unwrap();
        if s == 9 {
            assert_eq!(d.state, ProgressState::Stalled);
            assert_eq!(d.evidence[0].reason, Reason::StateStagnation);
        }
    }
    let mut m = monitor();
    for s in [1, 5, 9, 13] {
        let mut e = verified(s, 4);
        e.verification.as_mut().unwrap().observation_id = "cached".into();
        e.observation = Observation::default();
        assert_eq!(m.observe(e).unwrap().state, ProgressState::Healthy);
    }
}
#[test]
fn regression_and_scope_switch_are_distinct() {
    let mut m = monitor();
    m.observe(verified(1, 7)).unwrap();
    m.observe(verified(2, 2)).unwrap();
    let d = m.observe(verified(3, 6)).unwrap();
    assert_eq!(d.state, ProgressState::Regressing);
    assert_eq!(d.evidence[0].steps, vec![2, 3]);
    let mut other = verified(4, 20);
    other.verification.as_mut().unwrap().scope = "suite-B".into();
    assert_eq!(m.observe(other).unwrap().state, ProgressState::Healthy);
}
#[test]
fn state_oscillation_warns_without_claiming_goal_failure() {
    let mut m = monitor();
    for s in 1..=6 {
        let mut e = event(s, false);
        e.action.fingerprint = format!("action-{s}");
        e.state_fingerprint = Some(format!("state-{}", s % 2));
        let d = m.observe(e).unwrap();
        if s == 6 {
            assert_eq!(d.state, ProgressState::Warning);
            assert_eq!(d.evidence[0].reason, Reason::StateOscillation);
        }
    }
}
#[test]
fn invalid_events_do_not_mutate_history() {
    let mut m = monitor();
    m.observe(event(1, true)).unwrap();
    let mut invalid = event(2, true);
    invalid.run_id = "another-run".into();
    assert!(m.observe(invalid).is_err());
    assert!(m.observe(event(1, true)).is_err());
    assert_eq!(
        m.observe(event(2, true)).unwrap().state,
        ProgressState::Healthy
    );
    assert_eq!(
        monitor().observe(event(3, true)).unwrap().state,
        ProgressState::Healthy
    );
}
#[test]
fn conflicting_cached_verification_rejected_without_consuming_step() {
    let mut m = monitor();
    m.observe(verified(1, 4)).unwrap();
    let mut bad = verified(2, 5);
    bad.verification.as_mut().unwrap().observation_id = "test-1".into();
    assert!(m.observe(bad).is_err());
    assert!(m.observe(verified(2, 3)).unwrap().verified_progress);
}
#[test]
fn old_errors_expire_outside_window() {
    let mut m = Monitor::new(
        "test".into(),
        MonitorConfig {
            window: 4,
            ..Default::default()
        },
    )
    .unwrap();
    m.observe(event(1, true)).unwrap();
    m.observe(event(2, true)).unwrap();
    for s in 3..10 {
        let mut e = event(s, false);
        e.action.fingerprint = s.to_string();
        m.observe(e).unwrap();
    }
    assert_eq!(
        m.observe(event(10, true)).unwrap().state,
        ProgressState::Healthy
    );
}
#[test]
fn policy_defaults_observe_and_opt_in_has_cooldown_and_cap() {
    let mut m = monitor();
    let mut passive = Policy::new(PolicyConfig::default()).unwrap();
    let mut active = Policy::new(PolicyConfig {
        replan: true,
        cooldown_steps: 3,
        max_replans: 2,
        stop_at_step: Some(10),
    })
    .unwrap();
    let mut replans = vec![];
    for s in 1..=10 {
        let d = m.observe(event(s, true)).unwrap();
        assert_eq!(passive.apply(&d).unwrap().kind, InterventionKind::Observe);
        let i = active.apply(&d).unwrap();
        if i.kind == InterventionKind::Replan {
            replans.push(s);
            assert!(i.feedback.unwrap().contains("steps"));
        }
        if s == 10 {
            assert_eq!(i.kind, InterventionKind::Stop);
        }
    }
    assert_eq!(replans, vec![3, 6]);
}
#[test]
fn config_and_schema_are_strict() {
    assert!(
        Monitor::new(
            "test".into(),
            MonitorConfig {
                repetitions: 0,
                ..Default::default()
            }
        )
        .is_err()
    );
    assert!(serde_json::from_str::<MonitorConfig>(r#"{"windwo":4}"#).is_err());
    let mut e = event(1, false);
    e.schema_version = 2;
    assert!(monitor().observe(e).is_err());
    let mut e = event(1, false);
    e.observation.error_fingerprint = Some("error".into());
    assert!(monitor().observe(e).is_err());
}
