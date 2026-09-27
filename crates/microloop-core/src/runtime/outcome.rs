//! Action outcome tracking: whether an adaptation actually helped.
//!
//! A stall episode groups the adaptations attempted while the run stays
//! degraded. Each attempt is judged after a horizon, so it is not condemned a
//! single step after it was made.
//!
//! "Improved" means progress improved after the action, not that the action
//! caused success. The distinction keeps the learning data honest.

use crate::event::ProgressState;
use crate::runtime::action::RuntimeAction;
use crate::runtime::state::RuntimeState;
use serde::{Deserialize, Serialize};

/// How an attempted action turned out.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ActionOutcome {
    /// Not yet judged; the evaluation horizon has not elapsed.
    #[default]
    Pending,
    /// Progress improved after the action.
    Improved,
    /// Progress was unchanged after the action.
    NoChange,
    /// Progress got worse after the action.
    Regressed,
}

impl ActionOutcome {
    /// True when the outcome counts against repeating the same action.
    pub fn is_failure(self) -> bool {
        matches!(self, ActionOutcome::NoChange | ActionOutcome::Regressed)
    }
}

/// One adaptation attempted during a stall episode.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct ActionAttempt {
    pub action: RuntimeAction,
    pub step: u64,
    pub progress_before: ProgressState,
    pub evaluate_at: u64,
    #[serde(default)]
    pub outcome: ActionOutcome,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub progress_after: Option<ProgressState>,
}

impl ActionAttempt {
    /// Judge the attempt against the progress observed after its horizon.
    pub(crate) fn evaluate(&mut self, status: ProgressState) {
        let before = self.progress_before.rank();
        let after = status.rank();
        self.outcome = match after.cmp(&before) {
            std::cmp::Ordering::Less => ActionOutcome::Improved,
            std::cmp::Ordering::Equal => ActionOutcome::NoChange,
            std::cmp::Ordering::Greater => ActionOutcome::Regressed,
        };
        self.progress_after = Some(status);
    }
}

/// A contiguous degraded stretch of a run.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct StallEpisode {
    pub entered_at: u64,
    pub runtime_at_entry: RuntimeState,
    pub attempts: Vec<ActionAttempt>,
}

impl StallEpisode {
    pub(crate) fn new(step: u64, runtime: &RuntimeState) -> Self {
        Self {
            entered_at: step,
            runtime_at_entry: runtime.clone(),
            attempts: Vec::new(),
        }
    }

    /// True when `action` has already been attempted this episode.
    pub fn attempted(&self, action: RuntimeAction) -> bool {
        self.attempts.iter().any(|attempt| attempt.action == action)
    }

    /// How many attempts of `action` ended in failure.
    pub fn failures(&self, action: RuntimeAction) -> usize {
        self.attempts
            .iter()
            .filter(|attempt| attempt.action == action && attempt.outcome.is_failure())
            .count()
    }

    /// True when every attempt of `action` has failed at least once.
    pub fn is_exhausted(&self, action: RuntimeAction) -> bool {
        self.failures(action) >= 1
    }

    pub(crate) fn record(
        &mut self,
        action: RuntimeAction,
        step: u64,
        status: ProgressState,
        evaluate_at: u64,
    ) {
        self.attempts.push(ActionAttempt {
            action,
            step,
            progress_before: status,
            evaluate_at,
            outcome: ActionOutcome::Pending,
            progress_after: None,
        });
    }

    /// Judge every pending attempt whose horizon has elapsed.
    pub(crate) fn evaluate_due(&mut self, step: u64, status: ProgressState) {
        for attempt in &mut self.attempts {
            if attempt.outcome == ActionOutcome::Pending && step >= attempt.evaluate_at {
                attempt.evaluate(status);
            }
        }
    }

    /// Mark remaining pending attempts improved, because progress recovered.
    pub(crate) fn close_improved(&mut self) {
        for attempt in &mut self.attempts {
            if attempt.outcome == ActionOutcome::Pending {
                attempt.outcome = ActionOutcome::Improved;
                attempt.progress_after = Some(ProgressState::Healthy);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn attempts_are_judged_against_their_horizon() {
        let mut episode = StallEpisode::new(10, &RuntimeState::default());
        episode.record(RuntimeAction::Replan, 10, ProgressState::Stalled, 13);
        episode.evaluate_due(12, ProgressState::Stalled);
        assert_eq!(episode.attempts[0].outcome, ActionOutcome::Pending);
        episode.evaluate_due(13, ProgressState::Stalled);
        assert_eq!(episode.attempts[0].outcome, ActionOutcome::NoChange);
    }

    #[test]
    fn failures_accumulate_and_exhaust_an_action() {
        let mut episode = StallEpisode::new(1, &RuntimeState::default());
        episode.record(RuntimeAction::Replan, 1, ProgressState::Stalled, 2);
        episode.evaluate_due(2, ProgressState::Stalled);
        assert!(episode.is_exhausted(RuntimeAction::Replan));
        assert_eq!(episode.failures(RuntimeAction::Replan), 1);
    }

    #[test]
    fn improvement_makes_an_attempt_succeed() {
        let mut attempt = ActionAttempt {
            action: RuntimeAction::EscalateModel,
            step: 5,
            progress_before: ProgressState::Stalled,
            evaluate_at: 8,
            outcome: ActionOutcome::Pending,
            progress_after: None,
        };
        attempt.evaluate(ProgressState::Healthy);
        assert_eq!(attempt.outcome, ActionOutcome::Improved);
    }

    #[test]
    fn closing_marks_pending_as_improved() {
        let mut episode = StallEpisode::new(1, &RuntimeState::default());
        episode.record(RuntimeAction::Replan, 1, ProgressState::Stalled, 9);
        episode.close_improved();
        assert_eq!(episode.attempts[0].outcome, ActionOutcome::Improved);
    }
}
