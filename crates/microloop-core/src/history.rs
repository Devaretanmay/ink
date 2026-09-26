//! Bounded trajectory history. Storage only; all classification lives in
//! [`crate::detectors`].

use crate::canonical;
use crate::event::Event;

/// Signals extracted from one [`Event`], cached for cheap detector comparisons.
#[derive(Clone, Debug, PartialEq)]
pub struct Record {
    pub step: u64,
    pub action: String,
    pub observation: String,
    pub norm_action: Option<String>,
    pub norm_observation: Option<String>,
    pub failed: bool,
    pub error_sig: Option<String>,
    /// Canonical serialization of `state`, for equality comparison.
    pub state_key: Option<String>,
    pub verifier: Option<String>,
    pub verification_id: Option<String>,
    pub failures: Option<u64>,
    /// True when this record carries a previously unseen verification sample.
    pub fresh_verification: bool,
}

impl Record {
    /// Extract signals from an event. `normalize` enables masked comparisons.
    pub fn from_event(event: &Event, normalize: bool) -> Self {
        let metadata = event.metadata.as_ref();
        let failed = is_failure(event);
        let error_sig = metadata
            .and_then(|map| map.get("error").cloned())
            .or_else(|| {
                if failed {
                    let signature = canonical::error_signature(&event.observation);
                    (!signature.is_empty()).then_some(signature)
                } else {
                    None
                }
            });
        let verifier = metadata.and_then(|map| map.get("verifier").cloned());
        let verification_id = metadata.and_then(|map| map.get("verification_id").cloned());
        let failures = failures_from(event);
        let state_key = event
            .state
            .as_ref()
            .and_then(|state| serde_json::to_string(state).ok());
        let (norm_action, norm_observation) = if normalize {
            (
                Some(canonical::normalize(&event.action)),
                Some(canonical::normalize(&event.observation)),
            )
        } else {
            (None, None)
        };
        Self {
            step: event.step,
            action: event.action.clone(),
            observation: event.observation.clone(),
            norm_action,
            norm_observation,
            failed,
            error_sig,
            state_key,
            verifier,
            verification_id,
            failures,
            fresh_verification: false,
        }
    }
}

/// Whether a step counts as a failure, by convention.
pub fn is_failure(event: &Event) -> bool {
    if let Some(metadata) = &event.metadata {
        if let Some(success) = metadata.get("success") {
            let value = success.trim().to_ascii_lowercase();
            return matches!(value.as_str(), "false" | "0" | "no" | "error");
        }
    }
    if let Some(metrics) = &event.metrics {
        if let Some(exit_code) = metrics.get("exit_code") {
            return *exit_code != 0.0;
        }
        if let Some(success) = metrics.get("success") {
            return *success == 0.0;
        }
    }
    false
}

/// Read the verifier failure count from `metrics.failures`, if present.
pub fn failures_from(event: &Event) -> Option<u64> {
    let value = event.metrics.as_ref()?.get("failures")?;
    if !value.is_finite() || *value < 0.0 {
        return None;
    }
    Some(value.round() as u64)
}

/// Bounded ring buffer of records.
#[derive(Debug, Default)]
pub struct History {
    records: Vec<Record>,
    window: usize,
}

impl History {
    pub fn new(window: usize) -> Self {
        Self {
            records: Vec::new(),
            window,
        }
    }

    pub fn push(&mut self, record: Record) {
        self.records.push(record);
        while self.records.len() > self.window {
            self.records.remove(0);
        }
    }

    pub fn clear(&mut self) {
        self.records.clear();
    }

    pub fn as_slice(&self) -> &[Record] {
        &self.records
    }

    pub fn iter(&self) -> std::slice::Iter<'_, Record> {
        self.records.iter()
    }

    pub fn len(&self) -> usize {
        self.records.len()
    }

    pub fn is_empty(&self) -> bool {
        self.records.is_empty()
    }
}
