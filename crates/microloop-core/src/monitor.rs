//! Orchestration facade: runs detection, then applies the policy.

use crate::engine::{Outcome, ProgressEngine};
use crate::event::{Event, Evidence, MonitorConfig, ProgressState, Reason};
use crate::policy::{InterventionAction, Policy, PolicyConfig};
use serde::{Deserialize, Serialize};

/// The primary developer API surface: status, reasons and intervention.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct Decision {
    pub step: u64,
    pub status: ProgressState,
    /// Internal detection reasons, surfaced for debugging.
    pub reasons: Vec<Reason>,
    /// Supporting detail for each reason.
    pub evidence: Vec<Evidence>,
    /// The configured intervention for this step.
    pub intervention: InterventionAction,
    /// Heuristic severity in `0.0..=1.0`, not a calibrated probability.
    pub severity: f64,
    /// True when a verifier reported objective improvement.
    pub verified_progress: bool,
    /// Recovery context to inject when the intervention is not `Observe`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub feedback: Option<String>,
}

/// Observes events and reports decisions.
pub struct Monitor {
    engine: ProgressEngine,
    policy: Policy,
}

impl Default for Monitor {
    fn default() -> Self {
        Self::new()
    }
}

impl Monitor {
    /// Monitor with default config and observation-only policy.
    pub fn new() -> Self {
        Self::with_config(MonitorConfig::default()).expect("default monitor config is valid")
    }

    /// Monitor with an explicit detection config and observation-only policy.
    pub fn with_config(config: MonitorConfig) -> Result<Self, String> {
        Self::with_policy(config, Policy::default())
    }

    /// Monitor with explicit detection config and policy.
    pub fn with_policy(config: MonitorConfig, policy: Policy) -> Result<Self, String> {
        Ok(Self {
            engine: ProgressEngine::new(config)?,
            policy,
        })
    }

    /// Convenience constructor from a policy config.
    pub fn from_policy_config(config: MonitorConfig, policy: PolicyConfig) -> Result<Self, String> {
        Self::with_policy(config, Policy::new(policy)?)
    }

    pub fn config(&self) -> &MonitorConfig {
        self.engine.config()
    }

    /// Observe a step and return the decision for it.
    pub fn observe(&mut self, event: Event) -> Result<Decision, String> {
        let outcome = self.engine.observe(event)?;
        Ok(self.decide(outcome))
    }

    fn decide(&mut self, outcome: Outcome) -> Decision {
        let mut decision = Decision {
            step: outcome.step,
            status: outcome.status,
            reasons: outcome.reasons,
            evidence: outcome.evidence,
            intervention: InterventionAction::Observe,
            severity: severity(outcome.status),
            verified_progress: outcome.verified_progress,
            feedback: None,
        };
        let action = self.policy.evaluate(&decision);
        decision.intervention = action;
        if action != InterventionAction::Observe {
            decision.feedback = Some(recovery_context(&decision));
        }
        decision
    }
}

/// Heuristic severity for a progress state.
pub fn severity(status: ProgressState) -> f64 {
    match status {
        ProgressState::Healthy => 0.0,
        ProgressState::Warning => 0.4,
        ProgressState::Stalled => 0.8,
        ProgressState::Regressing => 0.9,
    }
}

fn recovery_context(decision: &Decision) -> String {
    let mut feedback = String::from(
        "MICROLOOP RECOVERY SIGNAL\nObserved evidence (heuristic, not a root-cause diagnosis):\n",
    );
    for item in &decision.evidence {
        feedback.push_str(&format!(
            "- {:?} at steps {:?}: {}\n",
            item.reason, item.steps, item.detail
        ));
    }
    feedback.push_str(
        "Re-evaluate the approach using this evidence. Avoid repeating failed actions unless new evidence supports them.",
    );
    feedback
}
