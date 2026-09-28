//! Microloop core: compatibility trajectory engine behind the decision-JIT product.
//!
//! This crate watches an agent's execution trajectory, detects when the agent is
//! looping, stalled, regressing, or operating on stale state, and reports a
//! configured intervention when progress degrades. New integrations should use
//! the decision API (DecisionSite, fast paths, coverage); this surface stays for
//! existing trajectory users.
//!
//! Detection and intervention are deliberately separated:
//!
//! - [`Monitor`] observes a stream of [`Event`]s and classifies the trajectory
//!   into a [`ProgressState`].
//! - [`Policy`] decides which [`InterventionAction`] that state warrants.
//!
//! The runtime performs no network calls and executes no agent actions. It only
//! returns instructions; the host decides whether to act on them.
//!
//! # Public surface
//!
//! The supported API is [`Event`], [`Monitor`], [`Decision`], [`Policy`],
//! [`PolicyConfig`], [`MonitorConfig`], [`ProgressState`],
//! [`InterventionAction`], [`Reason`] and [`Evidence`]. Detection internals (the
//! engine, detectors, history window and canonicalization) are private and may
//! change in any release. Reach the runtime through [`Monitor`].
//!
//! # How a step is classified
//!
//! [`Monitor::observe`] validates the [`Event`], appends it to a bounded
//! history window, runs the detectors over that window, and synthesizes one
//! [`ProgressState`]. The [`Policy`] then maps that state to an
//! [`InterventionAction`]. Detection and policy are independent: detectors
//! produce evidence and a status, and only the policy decides whether to
//! observe, replan, or stop.
//!
//! The policy defaults to [`InterventionAction::Observe`] for every state.
//! Automatic `Replan` or `Stop` requires an explicit opt-in via [`PolicyConfig`],
//! and is additionally bounded by a cooldown and a per-run intervention cap.

mod canonical;
mod config;
mod detectors;
mod engine;
mod event;
mod history;
mod monitor;
mod policy;
mod runtime;

pub use config::MonitorConfig;
pub use event::{Event, Evidence, MetricMap, ProgressState, Reason, StringMap};
pub use monitor::{Decision, Monitor};
pub use policy::{InterventionAction, Policy, PolicyConfig};
pub use runtime::{
    ActionOutcome, ActionScore, Budget, Capabilities, CapabilityLevel, ControllerConfig,
    ControllerTrace, ProgressSnapshot, RecommendationReason, RuntimeAction, RuntimeController,
    RuntimeDecision, RuntimeState, ScoringConfig, Strategy, Usage,
};
