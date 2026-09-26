//! Monitor configuration: the tunable detection bounds.
//!
//! Kept apart from [`crate::event`] because these are detection thresholds, not
//! runtime events. Every field has a conservative default, and
//! [`MonitorConfig::validate`] rejects bounds that would make detection
//! meaningless or unbounded.

use serde::{Deserialize, Serialize};

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
        assert!(MonitorConfig {
            stagnation_steps: 0,
            ..Default::default()
        }
        .validate()
        .is_err());
    }

    #[test]
    fn unknown_fields_are_rejected() {
        assert!(serde_json::from_str::<MonitorConfig>(r#"{"windwo":4}"#).is_err());
    }
}
