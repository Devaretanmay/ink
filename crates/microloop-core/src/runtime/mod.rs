//! Runtime layer: what execution resources and conditions exist, and what
//! runtime action they warrant.
//!
//! This module exists so that progress detection and runtime adaptation stay
//! independent. Progress answers *is this run advancing?*; the runtime layer
//! answers *under what conditions is it advancing, and what should change?*
//!
//! Pass 1 defines the vocabulary ([`RuntimeAction`]) and the decision surface
//! ([`RuntimeController`] -> [`RuntimeDecision`]) without shipping the adaptive
//! actuators. Only [`RuntimeAction::Continue`], [`RuntimeAction::Replan`] and
//! [`RuntimeAction::Stop`] are currently produced; the remaining actions are
//! defined so later passes do not need another interface change.

pub(crate) mod action;
pub(crate) mod capabilities;
pub(crate) mod controller;
pub(crate) mod decision;
pub(crate) mod outcome;
pub(crate) mod rules;
pub(crate) mod scoring;
pub(crate) mod state;

pub use action::RuntimeAction;
pub use capabilities::{Capabilities, CapabilityLevel};
pub use controller::{ControllerConfig, RuntimeController};
pub use decision::{
    ActionScore, ControllerTrace, ProgressSnapshot, RecommendationReason, RuntimeDecision, Strategy,
};
pub use outcome::ActionOutcome;
pub use scoring::ScoringConfig;
pub use state::{Budget, RuntimeState, Usage};
