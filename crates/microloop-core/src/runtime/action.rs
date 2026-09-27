//! Runtime action vocabulary.
//!
//! The full set is defined now so that later passes add actuators without
//! another interface change. Only [`RuntimeAction::Continue`],
//! [`RuntimeAction::Replan`] and [`RuntimeAction::Stop`] are enabled in this
//! pass; the rest are marked experimental and are never recommended yet.

use serde::{Deserialize, Serialize};

/// What the runtime might change about a running execution.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeAction {
    /// Keep running under the current strategy.
    #[default]
    Continue,
    /// Change strategy: re-evaluate the approach.
    Replan,
    /// Move to a stronger model for the remaining work.
    EscalateModel,
    /// Move to a cheaper model for the remaining work.
    DeescalateModel,
    /// Compress context to reclaim window.
    CompactContext,
    /// Roll back to a previously recorded checkpoint.
    RestoreCheckpoint,
    /// Retry the last tool call.
    RetryTool,
    /// Branch and explore an alternative strategy.
    BranchStrategy,
    /// End the run.
    Stop,
}

impl RuntimeAction {
    /// Actions this release is allowed to recommend. Pass 2 added the first
    /// three actuators; checkpointing, tool retry and branching remain defined
    /// but unproduced.
    pub fn is_enabled(self) -> bool {
        matches!(
            self,
            RuntimeAction::Continue
                | RuntimeAction::Replan
                | RuntimeAction::EscalateModel
                | RuntimeAction::DeescalateModel
                | RuntimeAction::CompactContext
                | RuntimeAction::Stop
        )
    }

    /// Actions defined for later passes but not yet produced.
    pub fn is_experimental(self) -> bool {
        !self.is_enabled()
    }

    /// True when the action changes execution and must be performed by an
    /// adapter. `Continue` and `Stop` are host-loop control instead.
    pub fn is_actuator(self) -> bool {
        !matches!(self, RuntimeAction::Continue | RuntimeAction::Stop)
    }

    /// Lowercase name, matching the serialized form.
    pub fn as_str(self) -> &'static str {
        match self {
            RuntimeAction::Continue => "continue",
            RuntimeAction::Replan => "replan",
            RuntimeAction::EscalateModel => "escalate_model",
            RuntimeAction::DeescalateModel => "deescalate_model",
            RuntimeAction::CompactContext => "compact_context",
            RuntimeAction::RestoreCheckpoint => "restore_checkpoint",
            RuntimeAction::RetryTool => "retry_tool",
            RuntimeAction::BranchStrategy => "branch_strategy",
            RuntimeAction::Stop => "stop",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_shipped_actions_are_enabled() {
        let enabled: Vec<&str> = [
            RuntimeAction::Continue,
            RuntimeAction::Replan,
            RuntimeAction::EscalateModel,
            RuntimeAction::DeescalateModel,
            RuntimeAction::CompactContext,
            RuntimeAction::RestoreCheckpoint,
            RuntimeAction::RetryTool,
            RuntimeAction::BranchStrategy,
            RuntimeAction::Stop,
        ]
        .into_iter()
        .filter(|action| action.is_enabled())
        .map(RuntimeAction::as_str)
        .collect();
        assert_eq!(
            enabled,
            vec![
                "continue",
                "replan",
                "escalate_model",
                "deescalate_model",
                "compact_context",
                "stop"
            ]
        );
    }

    #[test]
    fn control_actions_are_not_actuators() {
        assert!(!RuntimeAction::Continue.is_actuator());
        assert!(!RuntimeAction::Stop.is_actuator());
        assert!(RuntimeAction::Replan.is_actuator());
        assert!(RuntimeAction::CompactContext.is_actuator());
    }

    #[test]
    fn experimental_actions_serialize_for_later_passes() {
        assert_eq!(
            serde_json::to_string(&RuntimeAction::CompactContext).unwrap(),
            "\"compact_context\""
        );
    }
}
