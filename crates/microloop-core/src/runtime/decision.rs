//! Runtime decision types: a clean progress snapshot and the recommendation the
//! controller derives from it.
//!
//! The controller operates on these types rather than parsing a legacy
//! [`crate::Decision`]. The legacy fields exist for compatibility; the snapshot
//! and recommendation are the forward surface.

use crate::event::{ProgressState, Reason};
use crate::runtime::action::RuntimeAction;
use serde::{Deserialize, Serialize};

/// A clean read of trajectory progress for one step.
///
/// `signals` are the internal detector reasons, kept as data so the controller
/// never needs to know how they were computed.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(default)]
pub struct ProgressSnapshot {
    pub state: ProgressState,
    pub signals: Vec<Reason>,
    pub step: u64,
    /// The step at which the current state began.
    pub since_step: u64,
    /// Change in verifier failure count against the best previous sample.
    /// Negative is improvement, positive is regression, `None` is unknown.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub verification_delta: Option<i64>,
}

/// Why a runtime action was recommended.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RecommendationReason {
    /// The run is advancing.
    Progressing,
    /// There is not enough evidence to say anything.
    #[default]
    NoEvidence,
    /// Progress degraded, but the policy is observing only or rate-limited.
    ObservationOnly,
    /// Recurring failure or a verification plateau.
    TrajectoryStalled,
    /// A verifier got worse.
    TrajectoryRegressing,
    /// The context window is under pressure.
    ContextPressure,
    /// Progress recovered after an earlier adaptation.
    ProgressRecovered,
    /// A configured budget is exhausted.
    BudgetExhausted,
    /// A configured hard step limit was reached.
    StepLimitReached,
    /// The needed action is not supported by the adapter.
    ActionUnsupported,
    /// Every actuator a viable action already failed this stall episode.
    ActionExhausted,
}

/// How the controller chooses among candidate actions.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Strategy {
    /// First permitted candidate in rule order (the Pass 2 baseline).
    #[default]
    Rule,
    /// Score every permitted candidate and pick the best.
    Scored,
}

/// The score the controller gave one candidate action.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(default)]
pub struct ActionScore {
    pub action: RuntimeAction,
    pub score: f32,
    pub expected_progress: f32,
    pub cost_penalty: f32,
    pub repetition_penalty: f32,
    pub risk_penalty: f32,
    pub reason: RecommendationReason,
    /// Short human-readable notes behind the score, for verbose output.
    pub notes: Vec<String>,
}

/// The candidates considered for one decision, and the one selected.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(default)]
pub struct ControllerTrace {
    pub strategy: Strategy,
    pub candidates: Vec<ActionScore>,
    pub selected: RuntimeAction,
}

/// The controller's recommendation for a step.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(default)]
pub struct RuntimeDecision {
    pub action: RuntimeAction,
    pub reason: RecommendationReason,
    /// How the action was chosen. Present for the scored strategy, absent for
    /// the rule strategy.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trace: Option<ControllerTrace>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn progress_snapshot_defaults_are_safe() {
        let snapshot = ProgressSnapshot::default();
        assert_eq!(snapshot.state, ProgressState::Healthy);
        assert!(snapshot.signals.is_empty());
        assert_eq!(snapshot.verification_delta, None);
    }

    #[test]
    fn default_recommendation_continues() {
        let decision = RuntimeDecision::default();
        assert_eq!(decision.action, RuntimeAction::Continue);
        assert_eq!(decision.reason, RecommendationReason::NoEvidence);
        assert!(decision.trace.is_none());
    }

    #[test]
    fn action_score_serializes_its_dimensions() {
        let score = ActionScore {
            action: RuntimeAction::CompactContext,
            score: 0.74,
            expected_progress: 0.9,
            cost_penalty: 0.15,
            repetition_penalty: 0.0,
            risk_penalty: 0.01,
            reason: RecommendationReason::ContextPressure,
            notes: vec!["context utilization high".into()],
        };
        let json = serde_json::to_value(&score).unwrap();
        assert_eq!(json["action"], "compact_context");
        assert_eq!(json["reason"], "context_pressure");
        assert!(json["notes"].is_array());
    }

    #[test]
    fn strategy_defaults_to_rule() {
        assert_eq!(Strategy::default(), Strategy::Rule);
    }
}
