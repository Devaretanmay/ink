//! Adapter capabilities and the capability levels an integration can reach.
//!
//! A runtime may support replanning but not switching models, or vice versa.
//! The controller must never recommend an action the host cannot perform, so
//! capabilities are carried as data and checked before a recommendation is
//! returned.

use crate::runtime::action::RuntimeAction;
use serde::{Deserialize, Serialize};

/// What actuators an adapter can perform. Conservative by default: only
/// replanning is assumed, because any host that can receive a recommendation
/// can re-plan.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct Capabilities {
    pub replan: bool,
    pub model_switch: bool,
    pub context_compaction: bool,
    pub checkpoint: bool,
    pub retry_tool: bool,
    pub branch_strategy: bool,
}

impl Default for Capabilities {
    fn default() -> Self {
        Self {
            replan: true,
            model_switch: false,
            context_compaction: false,
            checkpoint: false,
            retry_tool: false,
            branch_strategy: false,
        }
    }
}

impl Capabilities {
    /// Whether an adapter can perform `action`. `Continue` and `Stop` are
    /// always available: they are host-loop control, not actuators.
    pub fn supports(&self, action: RuntimeAction) -> bool {
        match action {
            RuntimeAction::Continue | RuntimeAction::Stop => true,
            RuntimeAction::Replan => self.replan,
            RuntimeAction::EscalateModel | RuntimeAction::DeescalateModel => self.model_switch,
            RuntimeAction::CompactContext => self.context_compaction,
            RuntimeAction::RestoreCheckpoint => self.checkpoint,
            RuntimeAction::RetryTool => self.retry_tool,
            RuntimeAction::BranchStrategy => self.branch_strategy,
        }
    }
}

/// How much runtime data an integration exposes. Higher levels unlock sharper
/// analysis; nothing is required to use Microloop.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum CapabilityLevel {
    /// `action` and `observation` only. Repetition and error patterns work.
    Signals = 0,
    /// Plus `state`, `metrics` or `metadata`. Progress is measurable.
    Progress = 1,
    /// Plus a runtime snapshot. Runtime adaptation becomes possible.
    Runtime = 2,
}

impl CapabilityLevel {
    pub fn as_str(self) -> &'static str {
        match self {
            CapabilityLevel::Signals => "signals",
            CapabilityLevel::Progress => "progress",
            CapabilityLevel::Runtime => "runtime",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_capabilities_allow_only_replan() {
        let capabilities = Capabilities::default();
        assert!(capabilities.supports(RuntimeAction::Replan));
        assert!(!capabilities.supports(RuntimeAction::EscalateModel));
        assert!(capabilities.supports(RuntimeAction::Stop));
    }

    #[test]
    fn unknown_capability_fields_are_rejected() {
        assert!(serde_json::from_str::<Capabilities>(r#"{"replanning":true}"#).is_err());
    }
}
