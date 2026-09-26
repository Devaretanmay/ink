use microloop::monitor::{Action, Event, Monitor, MonitorConfig, Observation, Verification};
use microloop::policy::{InterventionKind, Policy, PolicyConfig};

fn main() {
    println!("=== Microloop v0.3 Trajectory Engine Example ===");

    // Initialize Monitor and Policy
    let mut monitor = Monitor::new("run_example_001".into(), MonitorConfig::default()).unwrap();
    let mut policy = Policy::new(PolicyConfig {
        replan: true,
        cooldown_steps: 4,
        max_replans: 2,
        stop_at_step: Some(50),
    })
    .unwrap();

    // Simulate an agent trajectory with a recurring error and stagnation
    for step in 1..=5 {
        let event = Event {
            schema_version: 1,
            run_id: "run_example_001".into(),
            step,
            action: Action {
                name: "shell".into(),
                fingerprint: format!("edit_auth_attempt_{step}"),
                normalized_fingerprint: None,
            },
            observation: Observation {
                success: Some(false),
                fingerprint: Some("pytest_auth_failed".into()),
                error_fingerprint: Some("AssertionError:tests/test_auth.py:42".into()),
                normalized_fingerprint: None,
            },
            verification: Some(Verification {
                scope: "pytest:auth".into(),
                observation_id: format!("obs_{step}"),
                failures: 4, // Stagnant at 4 failures
            }),
            state_fingerprint: None,
        };

        let decision = monitor.observe(event).unwrap();
        let intervention = policy.apply(&decision).unwrap();

        println!(
            "Step {step}: State={:?}, Severity={:.2}, Intervention={:?}",
            decision.state, decision.score, intervention.kind
        );

        if intervention.kind == InterventionKind::Replan {
            println!("\n[MICROLOOP INTERVENTION TRIGGERED]");
            println!("{}\n", intervention.feedback.as_deref().unwrap_or(""));
        }
    }

    println!("=== Legacy Tool-Call Verification (Backward Compatibility) ===");
    let mut legacy_state = microloop::MicroloopState::new("max_repeats: 3").unwrap();
    for step in 1..=3 {
        let verdict = microloop::verify(&mut legacy_state, b"read_file", br#"{"path":"a.txt"}"#);
        println!("Legacy step {step}: verdict={verdict}");
    }
}
