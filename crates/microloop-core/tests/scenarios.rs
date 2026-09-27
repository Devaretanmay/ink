//! Deterministic controller scenarios.
//!
//! These are the Pass 3 acceptance scenarios: cheap, fixed situations with a
//! known right answer, used to compare the scored controller against the rule
//! baseline. They are not benchmarks; they are regression tests for judgement.

use microloop_core::{
    Budget, Capabilities, ControllerConfig, Event, Monitor, MonitorConfig, ProgressState,
    RecommendationReason, RuntimeAction, RuntimeState, Strategy,
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

fn stalled(step: u64, context: Option<(u64, u64)>, cost: Option<f64>) -> Event {
    let mut event = Event::new(step, "pytest tests/", "1 failed, 4 passed");
    event.metrics = Some(nums(&[("exit_code", 1.0)]));
    event.metadata = Some(text(&[("error", "AssertionError: test_admin.py:42")]));
    let (context_tokens, context_limit) = match context {
        Some((used, limit)) => (Some(used), Some(limit)),
        None => (None, None),
    };
    event.runtime = Some(RuntimeState {
        model: Some("sonnet".into()),
        context_tokens,
        context_limit,
        cost,
        ..Default::default()
    });
    event
}

fn progressing(step: u64, cost: Option<f64>) -> Event {
    let mut event = Event::new(step, format!("read file-{step}.rs"), "contents");
    event.runtime = Some(RuntimeState {
        model: Some("opus".into()),
        cost,
        ..Default::default()
    });
    event
}

fn scored_config() -> ControllerConfig {
    ControllerConfig {
        strategy: Strategy::Scored,
        stalled: RuntimeAction::Replan,
        regressing: RuntimeAction::Stop,
        cooldown_steps: 1,
        max_interventions: 100,
        ..Default::default()
    }
}

fn all_actuators() -> Capabilities {
    Capabilities {
        model_switch: true,
        context_compaction: true,
        ..Default::default()
    }
}

fn monitor(config: ControllerConfig, capabilities: Capabilities, budget: Budget) -> Monitor {
    Monitor::from_controller_config(MonitorConfig::default(), config, capabilities, budget).unwrap()
}

#[test]
fn scenario_a_cheap_model_stall_replan_fails_escalates() {
    let mut monitor = monitor(scored_config(), all_actuators(), Budget::default());
    assert_eq!(
        monitor.observe(stalled(1, None, None)).unwrap().status,
        ProgressState::Healthy
    );
    assert_eq!(
        monitor.observe(stalled(2, None, None)).unwrap().status,
        ProgressState::Healthy
    );
    let first = monitor.observe(stalled(3, None, None)).unwrap();
    assert_eq!(first.recommendation.action, RuntimeAction::Replan);
    // The replan is judged after its horizon (3 steps).
    let second = monitor.observe(stalled(6, None, None)).unwrap();
    assert_eq!(second.recommendation.action, RuntimeAction::EscalateModel);
}

#[test]
fn scenario_b_high_context_compacts_instead_of_escalating() {
    let mut monitor = monitor(scored_config(), all_actuators(), Budget::default());
    let mut last = None;
    for step in 1..=3 {
        last = Some(
            monitor
                .observe(stalled(step, Some((59_000, 64_000)), None))
                .unwrap(),
        );
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
fn scenario_c_expensive_model_on_sustained_progress_deescalates() {
    let mut monitor = monitor(scored_config(), all_actuators(), Budget::default());
    for step in 1..=2 {
        let _ = monitor.observe(stalled(step, None, None)).unwrap();
    }
    assert_eq!(
        monitor
            .observe(stalled(3, None, None))
            .unwrap()
            .recommendation
            .action,
        RuntimeAction::Replan
    );
    assert_eq!(
        monitor
            .observe(stalled(6, None, None))
            .unwrap()
            .recommendation
            .action,
        RuntimeAction::EscalateModel
    );

    let mut deescalated = false;
    for step in 10..=30 {
        let decision = monitor.observe(progressing(step, Some(1.2))).unwrap();
        if decision.recommendation.action == RuntimeAction::DeescalateModel {
            deescalated = true;
            break;
        }
    }
    assert!(deescalated, "sustained progress should de-escalate");
}

#[test]
fn scenario_d_nearly_exhausted_budget_does_not_escalate() {
    let budget = Budget {
        max_cost: Some(1.0),
        ..Default::default()
    };
    let mut monitor = monitor(scored_config(), all_actuators(), budget);
    let _ = monitor.observe(stalled(1, Some((10_000, 64_000)), Some(0.9)));
    let _ = monitor.observe(stalled(2, Some((10_000, 64_000)), Some(0.9)));
    let decision = monitor
        .observe(stalled(3, Some((10_000, 64_000)), Some(0.9)))
        .unwrap();
    assert_ne!(decision.recommendation.action, RuntimeAction::EscalateModel);
}

#[test]
fn scenario_e_exhausted_actions_do_not_cycle_forever() {
    let mut monitor = monitor(scored_config(), all_actuators(), Budget::default());
    for step in 1..=2 {
        let _ = monitor.observe(stalled(step, None, None)).unwrap();
    }
    assert_eq!(
        monitor
            .observe(stalled(3, None, None))
            .unwrap()
            .recommendation
            .action,
        RuntimeAction::Replan
    );
    assert_eq!(
        monitor
            .observe(stalled(6, None, None))
            .unwrap()
            .recommendation
            .action,
        RuntimeAction::EscalateModel
    );
    // Both actuators have now failed; the controller stops adapting.
    let decision = monitor.observe(stalled(12, None, None)).unwrap();
    assert_eq!(decision.recommendation.action, RuntimeAction::Continue);
    assert_eq!(
        decision.recommendation.reason,
        RecommendationReason::ActionExhausted
    );
}

#[test]
fn the_rule_baseline_still_follows_its_ladder() {
    // Same trajectory, rule strategy: replan then escalate, no trace.
    let mut config = scored_config();
    config.strategy = Strategy::Rule;
    let mut monitor = monitor(config, all_actuators(), Budget::default());
    for step in 1..=2 {
        let _ = monitor.observe(stalled(step, None, None)).unwrap();
    }
    assert_eq!(
        monitor
            .observe(stalled(3, None, None))
            .unwrap()
            .recommendation
            .action,
        RuntimeAction::Replan
    );
    assert_eq!(
        monitor
            .observe(stalled(6, None, None))
            .unwrap()
            .recommendation
            .action,
        RuntimeAction::EscalateModel
    );
    assert!(monitor
        .observe(stalled(12, None, None))
        .unwrap()
        .recommendation
        .trace
        .is_none());
}
