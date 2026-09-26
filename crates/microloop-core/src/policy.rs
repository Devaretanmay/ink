//! Recovery policy: maps a progress state to an intervention instruction.
//!
//! The policy is conservative by default: every state maps to
//! [`InterventionAction::Observe`]. Automatic `Replan` or `Stop` requires an
//! explicit opt-in. The policy never executes anything; it only returns the
//! action the host may choose to take.

use crate::event::ProgressState;
use crate::monitor::Decision;
use serde::{Deserialize, Serialize};

/// What the host is advised to do.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum InterventionAction {
    /// Continue, monitoring only.
    #[default]
    Observe,
    /// Steer the agent to re-evaluate and change strategy.
    Replan,
    /// Stop the run.
    Stop,
}

/// Policy configuration. Defaults to observation-only.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct PolicyConfig {
    pub healthy: InterventionAction,
    pub warning: InterventionAction,
    pub stalled: InterventionAction,
    pub regressing: InterventionAction,
    /// Minimum steps between non-observe interventions.
    pub cooldown_steps: u64,
    /// Maximum non-observe interventions per run.
    pub max_interventions: usize,
    /// Hard step budget; reaching it always yields `Stop`.
    pub stop_at_step: Option<u64>,
}

impl Default for PolicyConfig {
    fn default() -> Self {
        Self {
            healthy: InterventionAction::Observe,
            warning: InterventionAction::Observe,
            stalled: InterventionAction::Observe,
            regressing: InterventionAction::Observe,
            cooldown_steps: 5,
            max_interventions: 2,
            stop_at_step: None,
        }
    }
}

impl PolicyConfig {
    fn action_for(&self, status: ProgressState) -> InterventionAction {
        match status {
            ProgressState::Healthy => self.healthy,
            ProgressState::Warning => self.warning,
            ProgressState::Stalled => self.stalled,
            ProgressState::Regressing => self.regressing,
        }
    }
}

/// Stateful intervention policy.
#[derive(Debug)]
pub struct Policy {
    config: PolicyConfig,
    last_intervention: Option<u64>,
    interventions: usize,
}

impl Default for Policy {
    fn default() -> Self {
        Self::new(PolicyConfig::default()).expect("default policy config is valid")
    }
}

impl Policy {
    pub fn new(config: PolicyConfig) -> Result<Self, String> {
        if config.cooldown_steps == 0 {
            return Err("cooldown_steps must be positive".into());
        }
        if config.max_interventions == 0 {
            return Err("max_interventions must be positive".into());
        }
        Ok(Self {
            config,
            last_intervention: None,
            interventions: 0,
        })
    }

    pub fn config(&self) -> &PolicyConfig {
        &self.config
    }

    /// Decide what to do with a classified decision.
    pub fn evaluate(&mut self, decision: &Decision) -> InterventionAction {
        if self
            .config
            .stop_at_step
            .is_some_and(|limit| decision.step >= limit)
        {
            return InterventionAction::Stop;
        }
        let action = self.config.action_for(decision.status);
        if action == InterventionAction::Observe {
            return InterventionAction::Observe;
        }
        if decision.evidence.is_empty() {
            return InterventionAction::Observe;
        }
        if self
            .last_intervention
            .is_some_and(|step| decision.step.saturating_sub(step) < self.config.cooldown_steps)
        {
            return InterventionAction::Observe;
        }
        if self.interventions >= self.config.max_interventions {
            return InterventionAction::Observe;
        }
        self.last_intervention = Some(decision.step);
        self.interventions += 1;
        action
    }
}
