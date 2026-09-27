//! Orchestration facade: runs detection, then derives a runtime recommendation.

use std::collections::BTreeSet;

use crate::config::MonitorConfig;
use crate::engine::{Outcome, ProgressEngine};
use crate::event::{Event, Evidence, ProgressState, Reason};
use crate::policy::{InterventionAction, Policy, PolicyConfig};
use crate::runtime::action::RuntimeAction;
use crate::runtime::capabilities::Capabilities;
use crate::runtime::controller::{ControllerConfig, RuntimeController};
use crate::runtime::decision::{ProgressSnapshot, RuntimeDecision};
use crate::runtime::state::{Budget, RuntimeState};
use serde::{Deserialize, Serialize};

/// The primary developer API surface: progress, runtime conditions and the
/// runtime recommendation, with the legacy status/intervention fields kept in
/// sync for compatibility.
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
    /// Categorical ordering for `status`, as a fixed lookup: `0.0` healthy,
    /// `0.4` warning, `0.8` stalled, `0.9` regressing. Not a probability, not a
    /// confidence, and not comparable across runs, so use `status` to branch on.
    pub severity: f64,
    /// True when a verifier reported objective improvement.
    pub verified_progress: bool,
    /// Recovery context to inject when the intervention is not `Observe`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub feedback: Option<String>,
    /// A clean read of progress for this step. The forward-facing progress API.
    #[serde(default)]
    pub progress: ProgressSnapshot,
    /// The runtime conditions reported with this step, or empty when the host
    /// supplied none.
    #[serde(default)]
    pub runtime: RuntimeState,
    /// What the runtime controller recommends. `action` mirrors `intervention`
    /// for the three actions enabled in this pass.
    #[serde(default)]
    pub recommendation: RuntimeDecision,
}

/// Observes events and reports decisions.
///
/// With [`Monitor::new`] the attached controller is observation-only, so
/// [`Decision::recommendation`] is always `continue` and
/// [`Decision::intervention`] is always [`InterventionAction::Observe`]. Build a
/// [`Policy`] with explicit [`PolicyConfig`] to opt in to `Replan` or `Stop`.
pub struct Monitor {
    engine: ProgressEngine,
    controller: RuntimeController,
    last_status: Option<ProgressState>,
    since_step: u64,
}

impl Default for Monitor {
    fn default() -> Self {
        Self::new()
    }
}

impl Monitor {
    /// Monitor with default config and observation-only controller.
    pub fn new() -> Self {
        Self::with_config(MonitorConfig::default()).expect("default monitor config is valid")
    }

    /// Monitor with an explicit detection config and observation-only policy.
    pub fn with_config(config: MonitorConfig) -> Result<Self, String> {
        Self::with_policy(config, Policy::default())
    }

    /// Monitor with explicit detection config and policy, default capabilities
    /// and no budget.
    pub fn with_policy(config: MonitorConfig, policy: Policy) -> Result<Self, String> {
        Self::from_controller_config(
            config,
            ControllerConfig::from(policy.config().clone()),
            Capabilities::default(),
            Budget::default(),
        )
    }

    /// Convenience constructor from a policy config.
    pub fn from_policy_config(config: MonitorConfig, policy: PolicyConfig) -> Result<Self, String> {
        Self::with_policy(config, Policy::new(policy)?)
    }

    /// Monitor with explicit policy, adapter capabilities and budget.
    pub fn with_runtime(
        config: MonitorConfig,
        policy: Policy,
        capabilities: Capabilities,
        budget: Budget,
    ) -> Result<Self, String> {
        Self::from_controller_config(
            config,
            ControllerConfig::from(policy.config().clone()),
            capabilities,
            budget,
        )
    }

    /// Convenience constructor from a policy config, capabilities and budget.
    pub fn from_runtime_config(
        config: MonitorConfig,
        policy: PolicyConfig,
        capabilities: Capabilities,
        budget: Budget,
    ) -> Result<Self, String> {
        Self::with_runtime(config, Policy::new(policy)?, capabilities, budget)
    }

    /// Monitor from an explicit controller configuration, capabilities and
    /// budget. This is the constructor the Python bindings use.
    pub fn from_controller_config(
        config: MonitorConfig,
        controller_config: ControllerConfig,
        capabilities: Capabilities,
        budget: Budget,
    ) -> Result<Self, String> {
        Ok(Self {
            engine: ProgressEngine::new(config)?,
            controller: RuntimeController::with_config(controller_config, capabilities, budget)?,
            last_status: None,
            since_step: 0,
        })
    }

    pub fn config(&self) -> &MonitorConfig {
        self.engine.config()
    }

    /// The controller currently attached, for introspection.
    pub fn controller(&self) -> &RuntimeController {
        &self.controller
    }

    /// Observe a step and return the decision for it.
    pub fn observe(&mut self, event: Event) -> Result<Decision, String> {
        self.observe_with_available(event, None)
    }

    /// Observe a step, telling the controller which actuator actions the adapter
    /// can perform right now. Availability gates actuators only.
    pub fn observe_with_available(
        &mut self,
        event: Event,
        available: Option<Vec<RuntimeAction>>,
    ) -> Result<Decision, String> {
        let runtime = event.runtime.clone().unwrap_or_default();
        let outcome = self.engine.observe(event)?;
        Ok(self.decide(outcome, runtime, available))
    }

    fn decide(
        &mut self,
        outcome: Outcome,
        mut runtime: RuntimeState,
        available: Option<Vec<RuntimeAction>>,
    ) -> Decision {
        if self.last_status != Some(outcome.status) {
            self.last_status = Some(outcome.status);
            self.since_step = outcome.step;
        }
        runtime.fill_budget(self.controller.budget());
        let progress = ProgressSnapshot {
            state: outcome.status,
            signals: outcome.reasons.clone(),
            step: outcome.step,
            since_step: self.since_step,
            verification_delta: outcome.verification_delta,
        };
        let mut decision = Decision {
            step: outcome.step,
            status: outcome.status,
            reasons: outcome.reasons,
            evidence: outcome.evidence,
            intervention: InterventionAction::Observe,
            severity: severity(outcome.status),
            verified_progress: outcome.verified_progress,
            feedback: None,
            progress,
            runtime: runtime.clone(),
            recommendation: RuntimeDecision::default(),
        };
        let available: Option<BTreeSet<RuntimeAction>> =
            available.map(|actions| actions.into_iter().collect());
        let recommendation =
            self.controller
                .decide_with_available(&decision, &runtime, available.as_ref());
        decision.intervention = match recommendation.action {
            RuntimeAction::Replan => InterventionAction::Replan,
            RuntimeAction::Stop => InterventionAction::Stop,
            _ => InterventionAction::Observe,
        };
        if decision.intervention != InterventionAction::Observe {
            decision.feedback = Some(recovery_context(&decision));
        }
        decision.recommendation = recommendation;
        decision
    }
}

/// Fixed status-to-number lookup backing [`Decision::severity`].
pub(crate) fn severity(status: ProgressState) -> f64 {
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
