//! Minimal Microloop usage: watch a trajectory and react to interventions.

use microloop_core::{Event, InterventionAction, Monitor, MonitorConfig};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut monitor = Monitor::with_config(MonitorConfig::default())?;

    for step in 1..=8u64 {
        let mut event = Event::new(step, "pytest tests/", "1 failed, 4 passed");
        event.metrics = Some([("exit_code".to_string(), 1.0)].into_iter().collect());
        event.metadata = Some(
            [(
                "error".to_string(),
                "AssertionError: test_admin.py:42".to_string(),
            )]
            .into_iter()
            .collect(),
        );

        let decision = monitor.observe(event)?;
        println!(
            "step {:>2}  {:<10} severity {:.1}  {:?}",
            decision.step,
            decision.status.as_str(),
            decision.severity,
            decision.reasons
        );
        if decision.intervention != InterventionAction::Observe {
            println!(
                "intervention: {:?}\n{}",
                decision.intervention,
                decision.feedback.unwrap_or_default()
            );
        }
    }
    Ok(())
}
