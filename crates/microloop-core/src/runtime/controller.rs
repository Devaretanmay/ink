//! Runtime controller: turns a progress snapshot and runtime state into a
//! runtime recommendation.
//!
//! Two strategies share one gating and decision surface:
//!
//! - **Rule** (the Pass 2 baseline): take the first permitted action in rule
//!   order. Kept intact so it can be compared against the scored controller.
//! - **Scored** (Pass 3): score every permitted action and pick the best, with
//!   a stability margin, deterministic tie-breaking and action-exhaustion.
//!
//! Rules define what is possible; [`crate::runtime::scoring`] decides what is
//! best. Gating -- budget, hard step limit, capabilities, availability,
//! cooldowns and the per-run cap -- is shared and unchanged.

use std::collections::BTreeSet;

use crate::event::ProgressState;
use crate::monitor::Decision;
use crate::policy::{InterventionAction, PolicyConfig};
use crate::runtime::action::RuntimeAction;
use crate::runtime::capabilities::Capabilities;
use crate::runtime::decision::{
    ActionScore, ControllerTrace, ProgressSnapshot, RecommendationReason, RuntimeDecision, Strategy,
};
use crate::runtime::outcome::StallEpisode;
use crate::runtime::rules;
use crate::runtime::scoring::{ActionScorer, ControllerInput, HeuristicScorer, ScoringConfig};
use crate::runtime::state::{Budget, RuntimeState};
use serde::{Deserialize, Serialize};

/// Controller configuration: the per-state base action plus the rule knobs.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct ControllerConfig {
    /// How to choose among candidates: `rule` or `scored`.
    pub strategy: Strategy,
    /// Tuning for the scored strategy.
    pub scoring: ScoringConfig,
    /// Base action when the run is healthy. `Continue` observes.
    pub healthy: RuntimeAction,
    /// Base action for a warning.
    pub warning: RuntimeAction,
    /// Base action for a stall. Any actuator here enables the adaptive ladder.
    pub stalled: RuntimeAction,
    /// Base action for a regression.
    pub regressing: RuntimeAction,
    /// Minimum steps between two non-`Continue` actions.
    pub cooldown_steps: u64,
    /// Maximum non-`Continue` actions per run.
    pub max_interventions: usize,
    /// Hard step limit; reaching it always yields `Stop`.
    pub stop_at_step: Option<u64>,
    /// Context utilization at which compaction becomes attractive.
    pub context_compaction_threshold: f64,
    /// Consecutive progressing steps before an escalation is de-escalated.
    pub deescalate_after: u64,
    /// Minimum steps between model changes (escalate or de-escalate).
    pub escalate_cooldown: u64,
}

impl Default for ControllerConfig {
    fn default() -> Self {
        Self {
            strategy: Strategy::Rule,
            scoring: ScoringConfig::default(),
            healthy: RuntimeAction::Continue,
            warning: RuntimeAction::Continue,
            stalled: RuntimeAction::Continue,
            regressing: RuntimeAction::Continue,
            cooldown_steps: 5,
            max_interventions: 2,
            stop_at_step: None,
            context_compaction_threshold: 0.8,
            deescalate_after: 3,
            escalate_cooldown: 8,
        }
    }
}

impl ControllerConfig {
    pub(crate) fn action_for(&self, status: ProgressState) -> RuntimeAction {
        match status {
            ProgressState::Healthy => self.healthy,
            ProgressState::Warning => self.warning,
            ProgressState::Stalled => self.stalled,
            ProgressState::Regressing => self.regressing,
        }
    }

    /// Validate the bounds that would otherwise make the controller incoherent.
    pub fn validate(&self) -> Result<(), String> {
        if self.cooldown_steps == 0 {
            return Err("cooldown_steps must be positive".into());
        }
        if self.max_interventions == 0 {
            return Err("max_interventions must be positive".into());
        }
        if self.deescalate_after == 0 {
            return Err("deescalate_after must be positive".into());
        }
        if !(0.0..=1.0).contains(&self.context_compaction_threshold) {
            return Err("context_compaction_threshold must be in 0.0..=1.0".into());
        }
        self.scoring.validate()?;
        Ok(())
    }
}

impl From<InterventionAction> for RuntimeAction {
    fn from(action: InterventionAction) -> Self {
        match action {
            InterventionAction::Observe => RuntimeAction::Continue,
            InterventionAction::Replan => RuntimeAction::Replan,
            InterventionAction::Stop => RuntimeAction::Stop,
        }
    }
}

impl From<PolicyConfig> for ControllerConfig {
    fn from(config: PolicyConfig) -> Self {
        Self {
            healthy: config.healthy.into(),
            warning: config.warning.into(),
            stalled: config.stalled.into(),
            regressing: config.regressing.into(),
            cooldown_steps: config.cooldown_steps,
            max_interventions: config.max_interventions,
            stop_at_step: config.stop_at_step,
            ..Default::default()
        }
    }
}

/// Stateful adaptation bookkeeping for gating, outcomes and cooldowns.
#[derive(Clone, Debug, Default)]
pub(crate) struct ControllerState {
    pub(crate) progressing_streak: u64,
    pub(crate) escalated: bool,
    pub(crate) last_action_step: Option<u64>,
    pub(crate) last_escalation_step: Option<u64>,
    pub(crate) actions_taken: usize,
    /// The degraded stretch currently in progress, if any.
    pub(crate) stall_episode: Option<StallEpisode>,
}

impl ControllerState {
    pub(crate) fn attempted(&self, action: RuntimeAction) -> bool {
        self.stall_episode
            .as_ref()
            .is_some_and(|episode| episode.attempted(action))
    }

    pub(crate) fn replanned_this_stall(&self) -> bool {
        self.attempted(RuntimeAction::Replan)
    }

    pub(crate) fn compacted_this_stall(&self) -> bool {
        self.attempted(RuntimeAction::CompactContext)
    }

    pub(crate) fn failures(&self, action: RuntimeAction) -> usize {
        self.stall_episode
            .as_ref()
            .map_or(0, |episode| episode.failures(action))
    }

    pub(crate) fn is_exhausted(&self, action: RuntimeAction) -> bool {
        self.stall_episode
            .as_ref()
            .is_some_and(|episode| episode.is_exhausted(action))
    }

    pub(crate) fn recent_actions(&self) -> Vec<RuntimeAction> {
        self.stall_episode
            .as_ref()
            .map(|episode| episode.attempts.iter().map(|a| a.action).collect())
            .unwrap_or_default()
    }
}

/// Runtime controller. Defaults to observation-only.
#[derive(Default)]
pub struct RuntimeController {
    config: ControllerConfig,
    capabilities: Capabilities,
    budget: Budget,
    state: ControllerState,
    scorer: HeuristicScorer,
}

impl RuntimeController {
    /// Controller from an explicit configuration and no capabilities or budget.
    pub fn new(config: ControllerConfig) -> Result<Self, String> {
        config.validate()?;
        Ok(Self {
            config,
            ..Default::default()
        })
    }

    /// Controller from explicit configuration, capabilities and budget.
    pub fn with_config(
        config: ControllerConfig,
        capabilities: Capabilities,
        budget: Budget,
    ) -> Result<Self, String> {
        config.validate()?;
        Ok(Self {
            config,
            capabilities,
            budget,
            state: ControllerState::default(),
            scorer: HeuristicScorer,
        })
    }

    /// Convenience constructor from a compatibility policy.
    pub fn with_policy(policy: &crate::policy::Policy) -> Result<Self, String> {
        Self::new(ControllerConfig::from(policy.config().clone()))
    }

    pub fn config(&self) -> &ControllerConfig {
        &self.config
    }

    pub fn capabilities(&self) -> &Capabilities {
        &self.capabilities
    }

    pub fn budget(&self) -> &Budget {
        &self.budget
    }

    /// Decide the runtime action for one step, with no per-step availability.
    pub fn decide(&mut self, decision: &Decision, runtime: &RuntimeState) -> RuntimeDecision {
        self.decide_with_available(decision, runtime, None)
    }

    /// Decide with an optional set of actions the adapter can perform right now.
    /// Availability gates actuators only; `continue` and `stop` are host-loop
    /// control and are never filtered.
    pub fn decide_with_available(
        &mut self,
        decision: &Decision,
        runtime: &RuntimeState,
        available: Option<&BTreeSet<RuntimeAction>>,
    ) -> RuntimeDecision {
        let progress = ProgressSnapshot {
            state: decision.status,
            signals: decision.reasons.clone(),
            step: decision.step,
            since_step: decision.progress.since_step,
            verification_delta: decision.progress.verification_delta,
        };
        self.track_status(&progress, runtime);
        self.evaluate_attempts(decision.step, decision.status);

        if let Some(reason) = self.budget_exceeded(decision.step, runtime) {
            return RuntimeDecision {
                action: RuntimeAction::Stop,
                reason,
                trace: None,
            };
        }
        if self
            .config
            .stop_at_step
            .is_some_and(|limit| decision.step >= limit)
        {
            return RuntimeDecision {
                action: RuntimeAction::Stop,
                reason: RecommendationReason::StepLimitReached,
                trace: None,
            };
        }

        match self.config.strategy {
            Strategy::Rule => self.decide_rule(decision, runtime, available),
            Strategy::Scored => self.decide_scored(&progress, decision, runtime, available),
        }
    }

    fn decide_rule(
        &mut self,
        decision: &Decision,
        runtime: &RuntimeState,
        available: Option<&BTreeSet<RuntimeAction>>,
    ) -> RuntimeDecision {
        let candidates = rules::candidates(decision.status, runtime, &self.config, &self.state);
        let mut blocked_by_capability = false;
        let mut blocked_by_rate = false;
        for action in &candidates {
            match self.permission(*action, decision.step, available) {
                Permission::Allowed => {
                    if *action == RuntimeAction::Continue {
                        return RuntimeDecision {
                            action: RuntimeAction::Continue,
                            reason: self.continue_reason(
                                decision,
                                blocked_by_capability,
                                blocked_by_rate,
                            ),
                            trace: None,
                        };
                    }
                    let reason = rules::reason_for(*action, decision.status);
                    self.record_action(*action, decision.step, decision.status);
                    return RuntimeDecision {
                        action: *action,
                        reason,
                        trace: None,
                    };
                }
                Permission::Unsupported => blocked_by_capability = true,
                Permission::RateLimited => blocked_by_rate = true,
            }
        }
        RuntimeDecision {
            action: RuntimeAction::Continue,
            reason: self.continue_reason(decision, blocked_by_capability, blocked_by_rate),
            trace: None,
        }
    }

    fn decide_scored(
        &mut self,
        progress: &ProgressSnapshot,
        decision: &Decision,
        runtime: &RuntimeState,
        available: Option<&BTreeSet<RuntimeAction>>,
    ) -> RuntimeDecision {
        let status = decision.status;
        let degraded = matches!(status, ProgressState::Stalled | ProgressState::Regressing);
        let mut blocked_by_capability = false;
        let mut blocked_by_rate = false;
        let mut exhausted = false;

        let mut permitted: Vec<RuntimeAction> = Vec::new();
        for action in dedup(rules::candidates(
            status,
            runtime,
            &self.config,
            &self.state,
        )) {
            match self.permission(action, decision.step, available) {
                Permission::Unsupported => {
                    blocked_by_capability = true;
                    continue;
                }
                Permission::RateLimited => {
                    blocked_by_rate = true;
                    continue;
                }
                Permission::Allowed => {}
            }
            if action.is_actuator() && self.state.is_exhausted(action) {
                exhausted = true;
                continue;
            }
            permitted.push(action);
        }

        // A scored controller may also decide to give up on a degraded run.
        if degraded
            && self.config.action_for(status) != RuntimeAction::Continue
            && !permitted.contains(&RuntimeAction::Stop)
            && matches!(
                self.permission(RuntimeAction::Stop, decision.step, available),
                Permission::Allowed
            )
        {
            permitted.push(RuntimeAction::Stop);
        }
        if !permitted.contains(&RuntimeAction::Continue) {
            permitted.push(RuntimeAction::Continue);
        }

        let scores: Vec<ActionScore> = {
            let input = ControllerInput {
                progress,
                runtime,
                state: &self.state,
                capabilities: &self.capabilities,
                budget: &self.budget,
                config: &self.config,
                recent_actions: self.state.recent_actions(),
            };
            permitted
                .iter()
                .map(|action| self.scorer.score(*action, &input))
                .collect()
        };

        let continue_score = scores
            .iter()
            .find(|score| score.action == RuntimeAction::Continue)
            .map_or(0.0, |score| score.score);

        // Strictly-greater comparison keeps tie-breaking deterministic: the
        // earliest candidate in rule order wins.
        let mut best: Option<&ActionScore> = None;
        for score in &scores {
            if score.action == RuntimeAction::Continue {
                continue;
            }
            if best.map_or(true, |current| score.score > current.score) {
                best = Some(score);
            }
        }

        let selected = best
            .filter(|score| score.score > continue_score + self.config.scoring.min_benefit)
            .map_or(RuntimeAction::Continue, |score| score.action);

        let reason = if selected == RuntimeAction::Continue {
            if exhausted {
                RecommendationReason::ActionExhausted
            } else {
                self.continue_reason(decision, blocked_by_capability, blocked_by_rate)
            }
        } else {
            rules::reason_for(selected, status)
        };

        if selected != RuntimeAction::Continue {
            self.record_action(selected, decision.step, status);
        }

        RuntimeDecision {
            action: selected,
            reason,
            trace: Some(ControllerTrace {
                strategy: Strategy::Scored,
                candidates: scores,
                selected,
            }),
        }
    }

    fn track_status(&mut self, progress: &ProgressSnapshot, runtime: &RuntimeState) {
        if progress.state == ProgressState::Healthy {
            self.state.progressing_streak += 1;
        } else {
            self.state.progressing_streak = 0;
        }
        let degraded = matches!(
            progress.state,
            ProgressState::Stalled | ProgressState::Regressing
        );
        match (degraded, self.state.stall_episode.is_some()) {
            (true, false) => {
                self.state.stall_episode = Some(StallEpisode::new(progress.step, runtime));
            }
            (false, true) => {
                if let Some(episode) = &mut self.state.stall_episode {
                    episode.close_improved();
                }
                self.state.stall_episode = None;
            }
            _ => {}
        }
    }

    fn evaluate_attempts(&mut self, step: u64, status: ProgressState) {
        if let Some(episode) = &mut self.state.stall_episode {
            episode.evaluate_due(step, status);
        }
    }

    fn permission(
        &self,
        action: RuntimeAction,
        step: u64,
        available: Option<&BTreeSet<RuntimeAction>>,
    ) -> Permission {
        if action == RuntimeAction::Continue {
            return Permission::Allowed;
        }
        if action.is_actuator() {
            if !self.capabilities.supports(action) {
                return Permission::Unsupported;
            }
            if available.is_some_and(|set| !set.contains(&action)) {
                return Permission::Unsupported;
            }
        }
        if self.state.actions_taken >= self.config.max_interventions {
            return Permission::RateLimited;
        }
        if let Some(last) = self.state.last_action_step {
            if step.saturating_sub(last) < self.config.cooldown_steps {
                return Permission::RateLimited;
            }
        }
        if matches!(
            action,
            RuntimeAction::EscalateModel | RuntimeAction::DeescalateModel
        ) {
            if let Some(last) = self.state.last_escalation_step {
                if step.saturating_sub(last) < self.config.escalate_cooldown {
                    return Permission::RateLimited;
                }
            }
        }
        Permission::Allowed
    }

    fn record_action(&mut self, action: RuntimeAction, step: u64, status: ProgressState) {
        match action {
            RuntimeAction::Continue => return,
            RuntimeAction::EscalateModel => {
                self.state.escalated = true;
                self.state.last_escalation_step = Some(step);
            }
            RuntimeAction::DeescalateModel => {
                self.state.escalated = false;
                self.state.last_escalation_step = Some(step);
            }
            _ => {}
        }
        if action.is_actuator() {
            if let Some(episode) = &mut self.state.stall_episode {
                let horizon = self.config.scoring.horizon(action);
                episode.record(action, step, status, step + horizon);
            }
        }
        self.state.last_action_step = Some(step);
        self.state.actions_taken += 1;
    }

    fn continue_reason(
        &self,
        decision: &Decision,
        blocked_by_capability: bool,
        blocked_by_rate: bool,
    ) -> RecommendationReason {
        if blocked_by_capability && !blocked_by_rate {
            return RecommendationReason::ActionUnsupported;
        }
        if decision.reasons.is_empty() {
            if decision.status == ProgressState::Healthy {
                RecommendationReason::Progressing
            } else {
                RecommendationReason::NoEvidence
            }
        } else {
            RecommendationReason::ObservationOnly
        }
    }

    fn budget_exceeded(&self, step: u64, runtime: &RuntimeState) -> Option<RecommendationReason> {
        if let (Some(max), Some(cost)) = (self.budget.max_cost, runtime.cost) {
            if cost >= max {
                return Some(RecommendationReason::BudgetExhausted);
            }
        }
        if let (Some(max), Some(tokens)) = (self.budget.max_tokens, runtime.total_tokens()) {
            if tokens >= max {
                return Some(RecommendationReason::BudgetExhausted);
            }
        }
        if let Some(max) = self.budget.max_steps {
            if step >= max {
                return Some(RecommendationReason::BudgetExhausted);
            }
        }
        if let (Some(max), Some(elapsed)) = (self.budget.max_seconds, runtime.elapsed_seconds) {
            if elapsed >= max {
                return Some(RecommendationReason::BudgetExhausted);
            }
        }
        None
    }
}

/// Remove duplicate actions, preserving first-seen order.
fn dedup(actions: Vec<RuntimeAction>) -> Vec<RuntimeAction> {
    let mut out: Vec<RuntimeAction> = Vec::new();
    for action in actions {
        if !out.contains(&action) {
            out.push(action);
        }
    }
    out
}

enum Permission {
    Allowed,
    Unsupported,
    RateLimited,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event::{Evidence, Reason};
    use crate::monitor::severity;
    use crate::runtime::decision::Strategy;

    fn decision(step: u64, status: ProgressState, signals: usize) -> Decision {
        Decision {
            step,
            status,
            reasons: vec![Reason::RepeatedError; signals],
            evidence: vec![
                Evidence {
                    reason: Reason::RepeatedError,
                    steps: vec![1, 2, 3],
                    detail: "same error".into(),
                };
                signals
            ],
            intervention: InterventionAction::Observe,
            severity: severity(status),
            verified_progress: false,
            feedback: None,
            progress: Default::default(),
            runtime: Default::default(),
            recommendation: Default::default(),
        }
    }

    fn adaptive() -> ControllerConfig {
        ControllerConfig {
            stalled: RuntimeAction::Replan,
            regressing: RuntimeAction::Stop,
            cooldown_steps: 1,
            max_interventions: 100,
            ..Default::default()
        }
    }

    fn scored_adaptive() -> ControllerConfig {
        ControllerConfig {
            strategy: Strategy::Scored,
            ..adaptive()
        }
    }

    fn context_bound() -> RuntimeState {
        RuntimeState {
            context_tokens: Some(60_000),
            context_limit: Some(64_000),
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

    #[test]
    fn default_controller_observes_only() {
        let mut controller = RuntimeController::default();
        let result = controller.decide(
            &decision(3, ProgressState::Stalled, 1),
            &RuntimeState::default(),
        );
        assert_eq!(result.action, RuntimeAction::Continue);
        assert_eq!(result.reason, RecommendationReason::ObservationOnly);
    }

    #[test]
    fn rule_strategy_walks_replan_then_escalate() {
        let mut controller =
            RuntimeController::with_config(adaptive(), all_actuators(), Budget::default()).unwrap();
        let first = controller.decide(
            &decision(3, ProgressState::Stalled, 1),
            &RuntimeState::default(),
        );
        assert_eq!(first.action, RuntimeAction::Replan);
        let second = controller.decide(
            &decision(10, ProgressState::Stalled, 1),
            &RuntimeState::default(),
        );
        assert_eq!(second.action, RuntimeAction::EscalateModel);
        assert!(second.trace.is_none(), "rule strategy emits no trace");
    }

    #[test]
    fn rule_context_pressure_compacts_first() {
        let mut controller = RuntimeController::with_config(
            adaptive(),
            Capabilities {
                context_compaction: true,
                ..Default::default()
            },
            Budget::default(),
        )
        .unwrap();
        let result = controller.decide(&decision(3, ProgressState::Stalled, 1), &context_bound());
        assert_eq!(result.action, RuntimeAction::CompactContext);
    }

    #[test]
    fn scored_context_pressure_beats_replan() {
        let mut controller =
            RuntimeController::with_config(scored_adaptive(), all_actuators(), Budget::default())
                .unwrap();
        let result = controller.decide(&decision(3, ProgressState::Stalled, 1), &context_bound());
        assert_eq!(result.action, RuntimeAction::CompactContext);
        let trace = result.trace.expect("scored trace");
        assert_eq!(trace.strategy, Strategy::Scored);
        assert_eq!(trace.selected, RuntimeAction::CompactContext);
        // compact, replan, escalate, continue, stop
        assert_eq!(trace.candidates.len(), 5);
        assert!(trace
            .candidates
            .iter()
            .any(|score| score.action == RuntimeAction::Stop));
    }

    #[test]
    fn scored_escalates_after_replan_fails() {
        let mut controller =
            RuntimeController::with_config(scored_adaptive(), all_actuators(), Budget::default())
                .unwrap();
        // Step 3: replan (nothing tried yet).
        let first = controller.decide(
            &decision(3, ProgressState::Stalled, 1),
            &RuntimeState::default(),
        );
        assert_eq!(first.action, RuntimeAction::Replan);
        // Step 6: replan judged no-change, escalate is now the best rung.
        let second = controller.decide(
            &decision(6, ProgressState::Stalled, 1),
            &RuntimeState::default(),
        );
        assert_eq!(second.action, RuntimeAction::EscalateModel);
    }

    #[test]
    fn scored_does_not_escalate_on_a_short_budget() {
        let budget = Budget {
            max_cost: Some(1.0),
            ..Default::default()
        };
        let mut controller =
            RuntimeController::with_config(scored_adaptive(), all_actuators(), budget).unwrap();
        let runtime = RuntimeState {
            cost: Some(0.9),
            context_tokens: Some(10_000),
            context_limit: Some(64_000),
            ..Default::default()
        };
        let decision = decision(3, ProgressState::Stalled, 1);
        let result = controller.decide(&decision, &runtime);
        assert_ne!(result.action, RuntimeAction::EscalateModel);
    }

    #[test]
    fn scored_deescalates_after_sustained_progress() {
        let mut controller =
            RuntimeController::with_config(scored_adaptive(), all_actuators(), Budget::default())
                .unwrap();
        controller.decide(
            &decision(3, ProgressState::Stalled, 1),
            &RuntimeState::default(),
        );
        controller.decide(
            &decision(6, ProgressState::Stalled, 1),
            &RuntimeState::default(),
        );
        assert!(controller.state.escalated);

        let mut deescalated = None;
        for step in 10..=30 {
            let result = controller.decide(
                &decision(step, ProgressState::Healthy, 0),
                &RuntimeState::default(),
            );
            if result.action == RuntimeAction::DeescalateModel {
                deescalated = Some(result);
                break;
            }
        }
        let result = deescalated.expect("deescalation after recovery");
        assert_eq!(result.reason, RecommendationReason::ProgressRecovered);
    }

    #[test]
    fn hysteresis_stops_a_marginal_action() {
        // With a huge stability margin nothing non-continue can win.
        let mut config = scored_adaptive();
        config.scoring.min_benefit = 10.0;
        let mut controller =
            RuntimeController::with_config(config, all_actuators(), Budget::default()).unwrap();
        let result = controller.decide(
            &decision(3, ProgressState::Stalled, 1),
            &RuntimeState::default(),
        );
        assert_eq!(result.action, RuntimeAction::Continue);
    }

    #[test]
    fn exhausted_actions_do_not_cycle_forever() {
        let mut controller =
            RuntimeController::with_config(scored_adaptive(), all_actuators(), Budget::default())
                .unwrap();
        // Replan (3), escalate (6), then both are exhausted; the run should stop
        // adapting rather than repeat them.
        assert_eq!(
            controller
                .decide(
                    &decision(3, ProgressState::Stalled, 1),
                    &RuntimeState::default()
                )
                .action,
            RuntimeAction::Replan
        );
        assert_eq!(
            controller
                .decide(
                    &decision(6, ProgressState::Stalled, 1),
                    &RuntimeState::default()
                )
                .action,
            RuntimeAction::EscalateModel
        );
        let result = controller.decide(
            &decision(12, ProgressState::Stalled, 1),
            &RuntimeState::default(),
        );
        assert_eq!(result.action, RuntimeAction::Continue);
        assert_eq!(result.reason, RecommendationReason::ActionExhausted);
    }

    #[test]
    fn tie_breaking_is_deterministic() {
        let mut config = scored_adaptive();
        config.scoring.min_benefit = 0.0;
        for _ in 0..5 {
            let mut controller =
                RuntimeController::with_config(config.clone(), all_actuators(), Budget::default())
                    .unwrap();
            let result = controller.decide(
                &decision(3, ProgressState::Stalled, 1),
                &RuntimeState::default(),
            );
            assert_eq!(result.action, RuntimeAction::Replan);
        }
    }

    #[test]
    fn exhausted_cost_budget_stops_the_run() {
        let budget = Budget {
            max_cost: Some(1.0),
            ..Default::default()
        };
        let mut controller =
            RuntimeController::with_config(adaptive(), Capabilities::default(), budget).unwrap();
        let runtime = RuntimeState {
            cost: Some(1.25),
            ..Default::default()
        };
        let result = controller.decide(&decision(3, ProgressState::Healthy, 0), &runtime);
        assert_eq!(result.action, RuntimeAction::Stop);
        assert_eq!(result.reason, RecommendationReason::BudgetExhausted);
    }

    #[test]
    fn unsupported_action_degrades_to_continue() {
        let capabilities = Capabilities {
            replan: false,
            ..Default::default()
        };
        let mut controller =
            RuntimeController::with_config(adaptive(), capabilities, Budget::default()).unwrap();
        let result = controller.decide(
            &decision(3, ProgressState::Stalled, 1),
            &RuntimeState::default(),
        );
        assert_eq!(result.action, RuntimeAction::Continue);
        assert_eq!(result.reason, RecommendationReason::ActionUnsupported);
    }

    #[test]
    fn policy_config_converts_into_controller_config() {
        let config = ControllerConfig::from(PolicyConfig {
            stalled: InterventionAction::Replan,
            regressing: InterventionAction::Stop,
            cooldown_steps: 3,
            max_interventions: 4,
            stop_at_step: Some(9),
            ..Default::default()
        });
        assert_eq!(config.strategy, Strategy::Rule);
        assert_eq!(config.stalled, RuntimeAction::Replan);
        assert_eq!(config.regressing, RuntimeAction::Stop);
        assert_eq!(config.cooldown_steps, 3);
    }

    #[test]
    fn invalid_config_is_rejected() {
        assert!(RuntimeController::new(ControllerConfig {
            cooldown_steps: 0,
            ..Default::default()
        })
        .is_err());
        assert!(RuntimeController::new(ControllerConfig {
            context_compaction_threshold: 1.5,
            ..Default::default()
        })
        .is_err());
        assert!(RuntimeController::new(ControllerConfig {
            scoring: ScoringConfig {
                min_benefit: -1.0,
                ..Default::default()
            },
            ..Default::default()
        })
        .is_err());
    }
}
