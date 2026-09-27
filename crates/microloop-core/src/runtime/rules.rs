//! Deterministic action selection.
//!
//! This is the Pass 2 policy: a small, ordered ladder that picks the cheapest
//! intervention first and escalates only when the cheaper rung did not recover.
//! It is deliberately a pure function of progress, runtime and controller state,
//! so Pass 3 can replace the ordering with a scored controller without changing
//! the gating or the decision surface.
//!
//! The ladder never runs unless the host opted in. With the default
//! configuration every degraded state maps to `continue`, and a state mapped to
//! `Stop` stops rather than adapting.

use crate::event::ProgressState;
use crate::runtime::action::RuntimeAction;
use crate::runtime::controller::{ControllerConfig, ControllerState};
use crate::runtime::decision::RecommendationReason;
use crate::runtime::state::RuntimeState;

/// Ordered candidate actions for a step, cheapest first. The controller takes
/// the first candidate it is permitted to perform and falls back to `Continue`.
pub(crate) fn candidates(
    status: ProgressState,
    runtime: &RuntimeState,
    config: &ControllerConfig,
    state: &ControllerState,
) -> Vec<RuntimeAction> {
    match status {
        ProgressState::Healthy | ProgressState::Warning => {
            let mut out = Vec::new();
            if state.escalated && state.progressing_streak >= config.deescalate_after {
                out.push(RuntimeAction::DeescalateModel);
            }
            out.push(RuntimeAction::Continue);
            out
        }
        ProgressState::Stalled | ProgressState::Regressing => {
            let base = config.action_for(status);
            if base == RuntimeAction::Continue {
                return vec![RuntimeAction::Continue];
            }
            if base == RuntimeAction::Stop {
                return vec![RuntimeAction::Stop];
            }

            // The host opted into adapting this state. Walk the ladder.
            let mut out = Vec::new();
            let context_bound = runtime
                .context_utilization()
                .is_some_and(|used| used >= config.context_compaction_threshold);
            if context_bound && !state.compacted_this_stall() {
                out.push(RuntimeAction::CompactContext);
            }
            if !state.replanned_this_stall() {
                out.push(RuntimeAction::Replan);
            }
            if !state.escalated {
                out.push(RuntimeAction::EscalateModel);
            }
            if !out.contains(&RuntimeAction::Replan) {
                out.push(RuntimeAction::Replan);
            }
            out.push(RuntimeAction::Continue);
            out
        }
    }
}

/// Why a candidate action was recommended, given the progress state.
pub(crate) fn reason_for(action: RuntimeAction, status: ProgressState) -> RecommendationReason {
    match action {
        RuntimeAction::CompactContext => RecommendationReason::ContextPressure,
        RuntimeAction::DeescalateModel => RecommendationReason::ProgressRecovered,
        RuntimeAction::Replan | RuntimeAction::EscalateModel => {
            if status == ProgressState::Regressing {
                RecommendationReason::TrajectoryRegressing
            } else {
                RecommendationReason::TrajectoryStalled
            }
        }
        RuntimeAction::Stop => match status {
            ProgressState::Regressing => RecommendationReason::TrajectoryRegressing,
            ProgressState::Stalled => RecommendationReason::TrajectoryStalled,
            _ => RecommendationReason::ObservationOnly,
        },
        _ => RecommendationReason::NoEvidence,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runtime::outcome::StallEpisode;

    fn state() -> ControllerState {
        ControllerState::default()
    }

    fn with_attempt(action: RuntimeAction) -> ControllerState {
        let mut state = ControllerState::default();
        let mut episode = StallEpisode::new(1, &RuntimeState::default());
        episode.record(action, 1, ProgressState::Stalled, 2);
        state.stall_episode = Some(episode);
        state
    }

    fn runtime(context: Option<(u64, u64)>) -> RuntimeState {
        let (context_tokens, context_limit) = match context {
            Some((used, limit)) => (Some(used), Some(limit)),
            None => (None, None),
        };
        RuntimeState {
            context_tokens,
            context_limit,
            ..Default::default()
        }
    }

    fn opted_in() -> ControllerConfig {
        ControllerConfig {
            stalled: RuntimeAction::Replan,
            regressing: RuntimeAction::Stop,
            ..Default::default()
        }
    }

    #[test]
    fn observe_only_configuration_never_adapts() {
        let config = ControllerConfig::default();
        assert_eq!(
            candidates(ProgressState::Stalled, &runtime(None), &config, &state()),
            vec![RuntimeAction::Continue]
        );
    }

    #[test]
    fn stalled_walks_replan_then_escalate() {
        let config = opted_in();
        let st = state();
        assert_eq!(
            candidates(ProgressState::Stalled, &runtime(None), &config, &st),
            vec![
                RuntimeAction::Replan,
                RuntimeAction::EscalateModel,
                RuntimeAction::Continue
            ]
        );
        let st = with_attempt(RuntimeAction::Replan);
        assert_eq!(
            candidates(ProgressState::Stalled, &runtime(None), &config, &st),
            vec![
                RuntimeAction::EscalateModel,
                RuntimeAction::Replan,
                RuntimeAction::Continue
            ]
        );
    }

    #[test]
    fn context_pressure_is_the_first_rung() {
        let config = opted_in();
        let out = candidates(
            ProgressState::Stalled,
            &runtime(Some((60_000, 64_000))),
            &config,
            &state(),
        );
        assert_eq!(out[0], RuntimeAction::CompactContext);
    }

    #[test]
    fn regressing_configured_as_stop_stops() {
        let config = opted_in();
        assert_eq!(
            candidates(ProgressState::Regressing, &runtime(None), &config, &state()),
            vec![RuntimeAction::Stop]
        );
    }

    #[test]
    fn deescalation_requires_a_prior_escalation() {
        let config = opted_in();
        let mut st = state();
        st.progressing_streak = 5;
        assert_eq!(
            candidates(ProgressState::Healthy, &runtime(None), &config, &st),
            vec![RuntimeAction::Continue]
        );
        st.escalated = true;
        assert_eq!(
            candidates(ProgressState::Healthy, &runtime(None), &config, &st),
            vec![RuntimeAction::DeescalateModel, RuntimeAction::Continue]
        );
    }

    #[test]
    fn reasons_track_the_selected_action() {
        assert_eq!(
            reason_for(RuntimeAction::CompactContext, ProgressState::Stalled),
            RecommendationReason::ContextPressure
        );
        assert_eq!(
            reason_for(RuntimeAction::DeescalateModel, ProgressState::Healthy),
            RecommendationReason::ProgressRecovered
        );
        assert_eq!(
            reason_for(RuntimeAction::EscalateModel, ProgressState::Regressing),
            RecommendationReason::TrajectoryRegressing
        );
    }
}
