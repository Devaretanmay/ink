//! Canonical structured event model.
//!
//! An [`Event`] is one agent step: what the agent did, what it saw, and the
//! optional structured context the host chose to attach. Detection bounds live
//! in [`crate::config::MonitorConfig`]; this module describes runtime events
//! only.

use crate::runtime::capabilities::CapabilityLevel;
use crate::runtime::state::RuntimeState;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Ordered string map, used for `state` and `metadata`.
pub type StringMap = BTreeMap<String, String>;
/// Ordered numeric map, used for `metrics`.
pub type MetricMap = BTreeMap<String, f64>;

/// A single agent step.
///
/// Signals are read by convention from [`Event::state`], [`Event::metrics`] and
/// [`Event::metadata`]:
///
/// | Key                          | Meaning                                     |
/// |------------------------------|---------------------------------------------|
/// | `metadata.success`           | `"false"`/`"0"` marks a failed step         |
/// | `metadata.error`             | stable error signature                     |
/// | `metadata.verifier`          | verifier scope name (e.g. `"pytest:auth"`) |
/// | `metadata.verification_id`   | unique id of one fresh verification run    |
/// | `metrics.exit_code`          | non-zero marks a failed step                |
/// | `metrics.failures`           | verifier failure count                     |
///
/// Missing signals are treated as unknown; the runtime never invents evidence.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
pub struct Event {
    /// Monotonic step index. Steps must strictly increase within a run.
    pub step: u64,
    /// What the agent did, e.g. `"shell"` or `"pytest tests/"`.
    pub action: String,
    /// What the agent observed, e.g. command output or a diff.
    pub observation: String,
    /// Environment snapshot for stagnation/oscillation detection.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub state: Option<StringMap>,
    /// Numeric measurements for this step.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub metrics: Option<MetricMap>,
    /// String attributes for this step.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub metadata: Option<StringMap>,
    /// Optional runtime snapshot: model, context, tokens, cost. Absent for
    /// integrations that expose only actions and observations.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub runtime: Option<RuntimeState>,
}

impl Event {
    /// Build a minimal event.
    pub fn new(step: u64, action: impl Into<String>, observation: impl Into<String>) -> Self {
        Self {
            step,
            action: action.into(),
            observation: observation.into(),
            ..Default::default()
        }
    }

    /// How much runtime data this step exposes. `Runtime` when a runtime
    /// snapshot is attached, `Progress` when state, metrics or metadata are, and
    /// `Signals` when only action and observation are.
    pub fn capability_level(&self) -> CapabilityLevel {
        if self.runtime.is_some() {
            CapabilityLevel::Runtime
        } else if self.state.is_some() || self.metrics.is_some() || self.metadata.is_some() {
            CapabilityLevel::Progress
        } else {
            CapabilityLevel::Signals
        }
    }
}

/// Public progress classification returned by the runtime.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ProgressState {
    #[default]
    Healthy,
    Warning,
    Stalled,
    Regressing,
}

impl ProgressState {
    /// Lowercase name, matching the serialized form.
    pub fn as_str(self) -> &'static str {
        match self {
            ProgressState::Healthy => "healthy",
            ProgressState::Warning => "warning",
            ProgressState::Stalled => "stalled",
            ProgressState::Regressing => "regressing",
        }
    }

    /// Categorical ordering, lower is better. Used to judge whether an
    /// adaptation improved progress, not as a probability.
    pub fn rank(self) -> u8 {
        match self {
            ProgressState::Healthy => 0,
            ProgressState::Warning => 1,
            ProgressState::Stalled => 2,
            ProgressState::Regressing => 3,
        }
    }
}

/// Internal detection reason surfaced for debugging under `reasons`.
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum Reason {
    RepeatedActionResult,
    NormalizedRepetition,
    RepeatedError,
    StateStagnation,
    StateOscillation,
    Regression,
}

/// One piece of supporting evidence for a [`Reason`].
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct Evidence {
    pub reason: Reason,
    pub steps: Vec<u64>,
    pub detail: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn event_defaults_are_empty() {
        let event = Event::new(1, "shell", "out");
        assert_eq!(event.step, 1);
        assert!(event.state.is_none());
        assert!(event.metrics.is_none());
        assert!(event.metadata.is_none());
        assert!(event.runtime.is_none());
        assert_eq!(event.capability_level(), CapabilityLevel::Signals);
    }

    #[test]
    fn capability_level_tracks_exposed_signals() {
        let mut event = Event::new(1, "shell", "out");
        event.state = Some(StringMap::new());
        assert_eq!(event.capability_level(), CapabilityLevel::Progress);
        event.runtime = Some(RuntimeState::default());
        assert_eq!(event.capability_level(), CapabilityLevel::Runtime);
    }
}
