//! Action scoring: decide *what is best* among the actions rules say are
//! possible.
//!
//! The scorer is deterministic and heuristic. It is split into four small
//! functions -- expected progress, cost, action history and runtime pressure --
//! rather than one formula with two dozen coefficients, so each is testable and
//! readable on its own.
//!
//! [`ActionScorer`] is the seam for Pass 4: the controller calls it through a
//! `&dyn ActionScorer`, so a learned scorer can replace [`HeuristicScorer`]
//! without touching the controller.

use crate::runtime::action::RuntimeAction;
use crate::runtime::capabilities::Capabilities;
use crate::runtime::controller::{ControllerConfig, ControllerState};
use crate::runtime::decision::{ActionScore, ProgressSnapshot};
use crate::runtime::rules;
use crate::runtime::state::{Budget, RuntimeState};
use serde::{Deserialize, Serialize};

/// Penalty applied per prior failed attempt of the same action in a stall episode.
const REPETITION_PENALTY: f32 = 0.35;

/// Tunables for the scored strategy. Weights themselves are fixed constants;
/// only the horizon and the stability threshold are configurable.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct ScoringConfig {
    /// A non-`continue` action must beat `continue` by this margin to be chosen.
    /// Prevents oscillation between actions with near-equal scores.
    pub min_benefit: f32,
    /// Steps allowed before a `replan` is judged.
    pub replan_horizon: u64,
    /// Steps allowed before a `compact_context` is judged.
    pub compact_horizon: u64,
    /// Steps allowed before an `escalate_model` is judged.
    pub escalate_horizon: u64,
    /// Steps allowed before a `deescalate_model` is judged.
    pub deescalate_horizon: u64,
}

impl Default for ScoringConfig {
    fn default() -> Self {
        Self {
            min_benefit: 0.15,
            replan_horizon: 3,
            compact_horizon: 2,
            escalate_horizon: 3,
            deescalate_horizon: 3,
        }
    }
}

impl ScoringConfig {
    /// How many steps an action gets before it is judged. Control actions have
    /// no horizon.
    pub fn horizon(&self, action: RuntimeAction) -> u64 {
        match action {
            RuntimeAction::Replan => self.replan_horizon,
            RuntimeAction::CompactContext => self.compact_horizon,
            RuntimeAction::EscalateModel => self.escalate_horizon,
            RuntimeAction::DeescalateModel => self.deescalate_horizon,
            _ => 0,
        }
    }

    pub(crate) fn validate(&self) -> Result<(), String> {
        if !self.min_benefit.is_finite() || self.min_benefit < 0.0 {
            return Err("min_benefit must be a non-negative number".into());
        }
        Ok(())
    }
}

/// Everything the scorer may read. This is the seam Pass 4 learns from.
pub(crate) struct ControllerInput<'a> {
    pub progress: &'a ProgressSnapshot,
    pub runtime: &'a RuntimeState,
    pub state: &'a ControllerState,
    pub capabilities: &'a Capabilities,
    pub budget: &'a Budget,
    pub config: &'a ControllerConfig,
    pub recent_actions: Vec<RuntimeAction>,
}

/// Scores one candidate action. `score` is the total; the fields break it down.
pub(crate) trait ActionScorer {
    fn score(&self, action: RuntimeAction, input: &ControllerInput<'_>) -> ActionScore;
}

/// The Pass 3 heuristic scorer.
#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct HeuristicScorer;

impl ActionScorer for HeuristicScorer {
    fn score(&self, action: RuntimeAction, input: &ControllerInput<'_>) -> ActionScore {
        let mut notes = Vec::new();
        let progress = score_progress(action, input, &mut notes);
        let cost = score_cost(action, input);
        let repetition = score_action_history(action, input, &mut notes);
        let (pressure_boost, risk) = score_runtime_pressure(action, input, &mut notes);

        let expected_progress = progress + pressure_boost;
        let total = expected_progress - cost - repetition - risk;
        ActionScore {
            action,
            score: total,
            expected_progress,
            cost_penalty: cost,
            repetition_penalty: repetition,
            risk_penalty: risk,
            reason: rules::reason_for(action, input.progress.state),
            notes,
        }
    }
}

/// How likely the action is to improve progress in the current condition.
fn score_progress(
    action: RuntimeAction,
    input: &ControllerInput<'_>,
    notes: &mut Vec<String>,
) -> f32 {
    if action.is_actuator() && !input.capabilities.supports(action) {
        // Candidates are gated before scoring, so this is only an annotation.
        notes.push("the adapter does not declare this action".into());
    }
    let status = input.progress.state;
    let degraded = matches!(
        status,
        crate::event::ProgressState::Stalled | crate::event::ProgressState::Regressing
    );
    if action == RuntimeAction::DeescalateModel {
        if !(input.state.escalated
            && input.state.progressing_streak >= input.config.deescalate_after)
        {
            return 0.0;
        }
        notes.push("progress is sustained after an escalation".into());
        return 0.7;
    }
    if action == RuntimeAction::Replan {
        if !degraded {
            return 0.0;
        }
        if input.state.replanned_this_stall() {
            notes.push("replan already tried this stall".into());
            return 0.1;
        }
        notes.push("replan not yet tried".into());
        return 0.7;
    }
    if action == RuntimeAction::EscalateModel {
        if !degraded {
            return 0.0;
        }
        if input.state.replanned_this_stall() {
            notes.push("replan did not recover".into());
            return 0.85;
        }
        return 0.4;
    }
    if action == RuntimeAction::CompactContext {
        let utilization = input.runtime.context_utilization().unwrap_or(0.0) as f32;
        if degraded && utilization >= input.config.context_compaction_threshold as f32 {
            notes.push("context utilization is high".into());
            return 0.7;
        }
        return 0.2;
    }
    match action {
        RuntimeAction::Continue => {
            if degraded {
                0.08
            } else {
                0.5
            }
        }
        RuntimeAction::Stop => {
            if status == crate::event::ProgressState::Regressing {
                notes.push("the run is regressing".into());
                0.3
            } else {
                0.05
            }
        }
        _ => 0.0,
    }
}

/// Relative controller cost of an action. Not dollars; de-escalation is a
/// reward because it saves cost.
fn score_cost(action: RuntimeAction, _input: &ControllerInput<'_>) -> f32 {
    match action {
        RuntimeAction::Continue | RuntimeAction::Stop => 0.0,
        RuntimeAction::Replan => 0.1,
        RuntimeAction::CompactContext => 0.15,
        RuntimeAction::EscalateModel => 0.25,
        RuntimeAction::DeescalateModel => -0.2,
        _ => 0.3,
    }
}

/// Penalize an action that already failed in the current stall episode.
fn score_action_history(
    action: RuntimeAction,
    input: &ControllerInput<'_>,
    notes: &mut Vec<String>,
) -> f32 {
    let attempted = input
        .recent_actions
        .iter()
        .filter(|candidate| **candidate == action)
        .count();
    let failures = input.state.failures(action);
    if failures > 0 {
        notes.push(
            format!("{action:?} failed {failures}/{attempted} attempts this stall").to_lowercase(),
        );
    }
    failures as f32 * REPETITION_PENALTY
}

/// Runtime conditions: context pressure, remaining budget and cost recovery.
fn score_runtime_pressure(
    action: RuntimeAction,
    input: &ControllerInput<'_>,
    notes: &mut Vec<String>,
) -> (f32, f32) {
    let mut boost = 0.0;
    let mut risk = 0.0;

    let utilization = input.runtime.context_utilization().unwrap_or(0.0) as f32;
    if action == RuntimeAction::CompactContext
        && utilization >= input.config.context_compaction_threshold as f32
    {
        boost += 0.2;
    }

    let pressure = budget_pressure(input);
    if action == RuntimeAction::EscalateModel && pressure > 0.0 {
        risk += pressure * 0.8;
        if pressure >= 0.5 {
            notes.push("remaining budget makes escalation risky".into());
        }
    }
    if action == RuntimeAction::Stop && pressure >= 0.9 {
        boost += 0.3;
    }

    if action == RuntimeAction::DeescalateModel && input.runtime.cost.is_some_and(|cost| cost > 0.0)
    {
        boost += 0.2;
        notes.push("de-escalation saves cost".into());
    }

    (boost, risk)
}

/// Fraction of the cost budget already spent, or `0.0` when unknown.
fn budget_pressure(input: &ControllerInput<'_>) -> f32 {
    match (input.budget.max_cost, input.runtime.cost) {
        (Some(max), Some(cost)) if max > 0.0 => (cost / max).clamp(0.0, 1.0) as f32,
        _ => 0.0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event::ProgressState;
    use crate::runtime::decision::ProgressSnapshot;
    use crate::runtime::outcome::StallEpisode;

    fn input<'a>(
        progress: &'a ProgressSnapshot,
        runtime: &'a RuntimeState,
        state: &'a ControllerState,
        capabilities: &'a Capabilities,
        budget: &'a Budget,
        config: &'a ControllerConfig,
    ) -> ControllerInput<'a> {
        ControllerInput {
            progress,
            runtime,
            state,
            capabilities,
            budget,
            config,
            recent_actions: Vec::new(),
        }
    }

    fn snapshot(status: ProgressState) -> ProgressSnapshot {
        ProgressSnapshot {
            state: status,
            ..Default::default()
        }
    }

    fn scored(action: RuntimeAction, input: &ControllerInput<'_>) -> ActionScore {
        HeuristicScorer.score(action, input)
    }

    #[test]
    fn replan_beats_escalation_before_replan_is_tried() {
        let progress = snapshot(ProgressState::Stalled);
        let runtime = RuntimeState::default();
        let state = ControllerState::default();
        let capabilities = Capabilities::default();
        let budget = Budget::default();
        let config = ControllerConfig {
            stalled: RuntimeAction::Replan,
            ..Default::default()
        };
        let input = input(&progress, &runtime, &state, &capabilities, &budget, &config);
        assert!(
            scored(RuntimeAction::Replan, &input).score
                > scored(RuntimeAction::EscalateModel, &input).score
        );
    }

    #[test]
    fn escalation_wins_after_replan_failed() {
        let progress = snapshot(ProgressState::Stalled);
        let mut episode = StallEpisode::new(10, &RuntimeState::default());
        episode.record(RuntimeAction::Replan, 10, ProgressState::Stalled, 11);
        episode.evaluate_due(11, ProgressState::Stalled);
        let state = ControllerState {
            stall_episode: Some(episode),
            ..Default::default()
        };
        let runtime = RuntimeState::default();
        let capabilities = Capabilities::default();
        let budget = Budget::default();
        let config = ControllerConfig {
            stalled: RuntimeAction::Replan,
            ..Default::default()
        };
        let input = input(&progress, &runtime, &state, &capabilities, &budget, &config);
        assert!(
            scored(RuntimeAction::EscalateModel, &input).score
                > scored(RuntimeAction::Replan, &input).score
        );
    }

    #[test]
    fn a_capped_budget_penalizes_escalation() {
        let progress = snapshot(ProgressState::Stalled);
        let state = ControllerState::default();
        let capabilities = Capabilities::default();
        let runtime = RuntimeState {
            cost: Some(0.9),
            ..Default::default()
        };
        let budget = Budget {
            max_cost: Some(1.0),
            ..Default::default()
        };
        let config = ControllerConfig {
            stalled: RuntimeAction::Replan,
            ..Default::default()
        };
        let input = input(&progress, &runtime, &state, &capabilities, &budget, &config);
        assert!(
            scored(RuntimeAction::Replan, &input).score
                > scored(RuntimeAction::EscalateModel, &input).score
        );
    }

    #[test]
    fn context_pressure_favours_compaction() {
        let progress = snapshot(ProgressState::Stalled);
        let runtime = RuntimeState {
            context_tokens: Some(60_000),
            context_limit: Some(64_000),
            ..Default::default()
        };
        let state = ControllerState::default();
        let capabilities = Capabilities::default();
        let budget = Budget::default();
        let config = ControllerConfig {
            stalled: RuntimeAction::Replan,
            ..Default::default()
        };
        let input = input(&progress, &runtime, &state, &capabilities, &budget, &config);
        assert!(
            scored(RuntimeAction::CompactContext, &input).score
                > scored(RuntimeAction::Replan, &input).score
        );
    }

    #[test]
    fn deescalation_is_rewarded_after_sustained_progress() {
        let progress = snapshot(ProgressState::Healthy);
        let runtime = RuntimeState {
            cost: Some(1.0),
            ..Default::default()
        };
        let state = ControllerState {
            escalated: true,
            progressing_streak: 5,
            ..Default::default()
        };
        let capabilities = Capabilities::default();
        let budget = Budget::default();
        let config = ControllerConfig::default();
        let input = input(&progress, &runtime, &state, &capabilities, &budget, &config);
        assert!(
            scored(RuntimeAction::DeescalateModel, &input).score
                > scored(RuntimeAction::Continue, &input).score
        );
    }

    #[test]
    fn horizons_are_per_action() {
        let config = ScoringConfig::default();
        assert_eq!(config.horizon(RuntimeAction::CompactContext), 2);
        assert_eq!(config.horizon(RuntimeAction::Replan), 3);
        assert_eq!(config.horizon(RuntimeAction::Continue), 0);
    }
}
