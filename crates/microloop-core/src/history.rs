//! Bounded trajectory history. Storage only; all classification lives in
//! [`crate::detectors`].

use crate::canonical;
use crate::event::Event;
use std::collections::VecDeque;

/// Signals extracted from one [`Event`], cached for cheap detector comparisons.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Record {
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
pub(crate) fn is_failure(event: &Event) -> bool {
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
pub(crate) fn failures_from(event: &Event) -> Option<u64> {
    let value = event.metrics.as_ref()?.get("failures")?;
    if !value.is_finite() || *value < 0.0 {
        return None;
    }
    Some(value.round() as u64)
}

/// Bounded FIFO window of records, oldest first.
///
/// A `VecDeque` so that eviction is O(1) at the front. The detectors require
/// oldest-first ordering, so the front is the eviction point; a `Vec` with
/// `remove(0)` would shift every remaining element on each step.
#[derive(Debug, Default)]
pub(crate) struct History {
    records: VecDeque<Record>,
    window: usize,
}

impl History {
    pub fn new(window: usize) -> Self {
        Self {
            records: VecDeque::with_capacity(window.min(1024)),
            window,
        }
    }

    /// Append a record, dropping the oldest once the window is full.
    pub fn push(&mut self, record: Record) {
        if self.window == 0 {
            return;
        }
        if self.records.len() == self.window {
            self.records.pop_front();
        }
        self.records.push_back(record);
    }

    pub fn clear(&mut self) {
        self.records.clear();
    }

    /// Oldest-first contiguous view of the window.
    ///
    /// The detectors take `&[Record]`. `make_contiguous` is a no-op when the
    /// deque is already contiguous and otherwise rotates it once, so this is
    /// O(1) amortized per call rather than O(n) like a `Vec::remove(0)` shift.
    pub fn window(&mut self) -> &[Record] {
        self.records.make_contiguous()
    }

    pub fn iter(&self) -> std::collections::vec_deque::Iter<'_, Record> {
        self.records.iter()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event::Event;

    fn rec(step: u64) -> Record {
        Record::from_event(&Event::new(step, "shell", "out"), false)
    }

    #[test]
    fn window_is_bounded_and_evicts_oldest_first() {
        let mut history = History::new(3);
        for step in 1..=10 {
            history.push(rec(step));
        }
        let steps: Vec<u64> = history.window().iter().map(|r| r.step).collect();
        assert_eq!(
            steps,
            vec![8, 9, 10],
            "oldest must be evicted, order preserved"
        );
    }

    #[test]
    fn zero_window_retains_nothing() {
        let mut history = History::new(0);
        history.push(rec(1));
        assert!(history.window().is_empty());
    }

    #[test]
    fn clear_empties_the_window() {
        let mut history = History::new(4);
        history.push(rec(1));
        history.clear();
        assert!(history.window().is_empty());
    }
}
