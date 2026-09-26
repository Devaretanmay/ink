//! Canonical structured event model.
//!
//! An [`Event`] is one agent step. It carries what the agent did, what it saw,
//! and optional structured context the host chose to attach. Signals are read
//! by convention from [`Event::state`], [`Event::metrics`] and
//! [`Event::metadata`]:
//!
//! | Key                                | Meaning                                   |
//! |------------------------------------|-------------------------------------------|
//! | `metadata.success`                 | `"false"`/`"0"` marks a failed step        |
//! | `metadata.error`                   | stable error signature                     |
//! | `metadata.verifier`                | verifier scope name (e.g. `"pytest:auth"`) |
//! | `metadata.verification_id`         | unique id of one fresh verification run    |
//! | `metrics.exit_code`                | non-zero marks a failed step               |
//! | `metrics.failures`                 | verifier failure count                     |
//!
//! Missing signals are treated as unknown; the runtime never invents evidence.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Ordered string map, used for `state` and `metadata`.
pub type StringMap = BTreeMap<String, String>;
/// Ordered numeric map, used for `metrics`.
pub type MetricMap = BTreeMap<String, f64>;

/// A single agent step.
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
}

/// Public progress classification returned by the runtime.
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ProgressState {
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

/// Tunable detection bounds. All fields have conservative defaults.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct MonitorConfig {
    /// Maximum number of recent steps retained.
    pub window: usize,
    /// Recurrence count required to call a repeated action/result or error.
    pub repetitions: usize,
    /// Step span over which an unchanged verifier is considered stagnant.
    pub stagnation_steps: u64,
    /// Fresh verifier samples required before stagnation can be reported.
    pub verification_samples: usize,
    /// Mask volatile tokens (paths, hashes, timestamps, PIDs) before comparison.
    pub normalization: bool,
}

impl Default for MonitorConfig {
    fn default() -> Self {
        Self {
            window: 32,
            repetitions: 3,
            stagnation_steps: 8,
            verification_samples: 3,
            normalization: true,
        }
    }
}

impl MonitorConfig {
    /// Validate bounds.
    pub fn validate(&self) -> Result<(), String> {
        if !(4..=4096).contains(&self.window) {
            return Err("window must be in 4..=4096".into());
        }
        if !(2..=self.window).contains(&self.repetitions) {
            return Err("repetitions must be in 2..=window".into());
        }
        if !(2..=self.window).contains(&self.verification_samples) {
            return Err("verification_samples must be in 2..=window".into());
        }
        if self.stagnation_steps == 0 {
            return Err("stagnation_steps must be positive".into());
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_config_is_valid() {
        assert!(MonitorConfig::default().validate().is_ok());
    }

    #[test]
    fn out_of_range_bounds_are_rejected() {
        assert!(MonitorConfig {
            window: 3,
            ..Default::default()
        }
        .validate()
        .is_err());
        assert!(MonitorConfig {
            repetitions: 1,
            ..Default::default()
        }
        .validate()
        .is_err());
    }

    #[test]
    fn event_defaults_are_empty() {
        let event = Event::new(1, "shell", "out");
        assert_eq!(event.step, 1);
        assert!(event.state.is_none());
        assert!(event.metrics.is_none());
        assert!(event.metadata.is_none());
    }
}
