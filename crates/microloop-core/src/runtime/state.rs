//! Runtime state: the resources and execution conditions behind progress.
//!
//! The host reports what it knows. Nothing here is required, and Microloop
//! never prices a model: `cost` is whatever the host measured. Missing fields
//! are unknown, not zero.

use serde::{Deserialize, Serialize};

/// Cumulative resource usage for a run.
///
/// Derived from [`RuntimeState`] so the runtime keeps one wire shape while the
/// learning loop (later pass) can treat usage as a first-class unit.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(default)]
pub struct Usage {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cost: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub input_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub steps: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub elapsed_seconds: Option<f64>,
}

/// Execution budgets. All optional; an unset bound is unconstrained.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct Budget {
    pub max_cost: Option<f64>,
    pub max_tokens: Option<u64>,
    pub max_steps: Option<u64>,
    pub max_seconds: Option<f64>,
}

impl Budget {
    /// True when no bound is configured.
    pub fn is_unconstrained(&self) -> bool {
        self.max_cost.is_none()
            && self.max_tokens.is_none()
            && self.max_steps.is_none()
            && self.max_seconds.is_none()
    }
}

/// A snapshot of the execution conditions reported with one step.
///
/// Every field is optional so integrations can adopt Microloop incrementally.
/// `cost` accepts the `estimated_cost` spelling on input for compatibility with
/// hosts that already name it that way.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(default)]
pub struct RuntimeState {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context_limit: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub input_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output_tokens: Option<u64>,
    #[serde(alias = "estimated_cost", skip_serializing_if = "Option::is_none")]
    pub cost: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub elapsed_seconds: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_calls: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub remaining_budget: Option<f64>,
}

impl RuntimeState {
    /// True when the host reported nothing for this step.
    pub fn is_empty(&self) -> bool {
        self == &Self::default()
    }

    /// Total tokens, when either side was reported.
    pub fn total_tokens(&self) -> Option<u64> {
        match (self.input_tokens, self.output_tokens) {
            (None, None) => None,
            (input, output) => Some(input.unwrap_or(0) + output.unwrap_or(0)),
        }
    }

    /// Fraction of the context window in use, when both sides are known.
    pub fn context_utilization(&self) -> Option<f64> {
        match (self.context_tokens, self.context_limit) {
            (Some(used), Some(limit)) if limit > 0 => Some(used as f64 / limit as f64),
            _ => None,
        }
    }

    /// Roll the runtime fields up into a [`Usage`].
    pub fn usage(&self) -> Usage {
        Usage {
            cost: self.cost,
            input_tokens: self.input_tokens,
            output_tokens: self.output_tokens,
            steps: None,
            elapsed_seconds: self.elapsed_seconds,
        }
    }

    /// Fill `remaining_budget` from a cost bound when the host did not supply
    /// one. Microloop only does this arithmetic; it never estimates prices.
    pub(crate) fn fill_budget(&mut self, budget: &Budget) {
        if self.remaining_budget.is_none() {
            if let (Some(max), Some(cost)) = (budget.max_cost, self.cost) {
                self.remaining_budget = Some((max - cost).max(0.0));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn runtime_state_tolerates_partial_input() {
        let state: RuntimeState =
            serde_json::from_str(r#"{"model":"sonnet","cost":0.82}"#).unwrap();
        assert_eq!(state.model.as_deref(), Some("sonnet"));
        assert_eq!(state.cost, Some(0.82));
        assert!(state.context_limit.is_none());
    }

    #[test]
    fn estimated_cost_is_an_accepted_alias() {
        let state: RuntimeState = serde_json::from_str(r#"{"estimated_cost":1.5}"#).unwrap();
        assert_eq!(state.cost, Some(1.5));
    }

    #[test]
    fn context_utilization_and_tokens() {
        let state = RuntimeState {
            context_tokens: Some(48_000),
            context_limit: Some(64_000),
            input_tokens: Some(100),
            output_tokens: Some(20),
            ..Default::default()
        };
        assert_eq!(state.total_tokens(), Some(120));
        assert_eq!(state.context_utilization(), Some(0.75));
    }

    #[test]
    fn remaining_budget_is_derived_from_cost_only() {
        let mut state = RuntimeState {
            cost: Some(0.5),
            ..Default::default()
        };
        state.fill_budget(&Budget {
            max_cost: Some(2.0),
            ..Default::default()
        });
        assert_eq!(state.remaining_budget, Some(1.5));
    }
}
