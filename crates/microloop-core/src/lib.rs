//! Microloop core: a local reliability runtime for autonomous agents.
//!
//! Microloop watches an agent's execution trajectory, detects when the agent is
//! looping, stalled, regressing, or operating on stale state, and reports a
//! configured intervention when progress degrades.
//!
//! Detection and intervention are deliberately separated:
//!
//! - [`Monitor`] observes a stream of [`Event`]s and classifies the trajectory
//!   into a [`ProgressState`].
//! - [`Policy`] decides which [`InterventionAction`] that state warrants.
//!
//! The runtime performs no network calls and executes no agent actions. It only
//! returns instructions; the host decides whether to act on them.

pub mod canonical;
pub mod detectors;
pub mod engine;
pub mod event;
pub mod history;
pub mod monitor;
pub mod policy;

pub use event::{Event, Evidence, MonitorConfig, ProgressState, Reason};
pub use monitor::{Decision, Monitor};
pub use policy::{InterventionAction, Policy, PolicyConfig};
