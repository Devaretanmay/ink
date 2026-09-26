//! Stagnation detection: fresh verifications report an unchanged failure count.

use crate::event::{Evidence, Reason};
use crate::history::Record;

/// Detect a verifier plateau. Requires `verification_samples` fresh samples in
/// the same scope spanning at least `min_span` steps, all with the same
/// non-zero failure count as the current step.
pub fn detect(
    history: &[Record],
    current: &Record,
    verification_samples: usize,
    min_span: u64,
) -> Option<Evidence> {
    let failures = current.failures?;
    if failures == 0 {
        return None;
    }
    let fresh: Vec<&Record> = history
        .iter()
        .filter(|record| {
            record.fresh_verification
                && record.failures.is_some()
                && record.verifier == current.verifier
        })
        .collect();
    if fresh.len() < verification_samples {
        return None;
    }
    let first = fresh.first()?;
    if current.step.saturating_sub(first.step) < min_span {
        return None;
    }
    if !fresh.iter().all(|record| record.failures == Some(failures)) {
        return None;
    }
    Some(Evidence {
        reason: Reason::StateStagnation,
        steps: fresh.iter().map(|record| record.step).collect(),
        detail: format!(
            "{} fresh verifications still report {} failures over {} steps",
            fresh.len(),
            failures,
            current.step - first.step
        ),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event::Event;
    use std::collections::BTreeMap;

    fn verified(step: u64, failures: u64, fresh: bool) -> Record {
        let mut event = Event::new(step, format!("action-{step}"), "");
        event.metrics = Some(
            [("failures".to_string(), failures as f64)]
                .into_iter()
                .collect(),
        );
        event.metadata = Some(
            [
                ("verifier".to_string(), "suite-A".to_string()),
                ("verification_id".to_string(), format!("run-{step}")),
            ]
            .into_iter()
            .collect::<BTreeMap<_, _>>(),
        );
        let mut record = Record::from_event(&event, true);
        record.fresh_verification = fresh;
        record
    }

    #[test]
    fn plateau_across_fresh_samples_is_detected() {
        let history = vec![
            verified(1, 4, true),
            verified(5, 4, true),
            verified(9, 4, true),
        ];
        let found = detect(&history, &history[2], 3, 8).expect("stagnant");
        assert_eq!(found.reason, Reason::StateStagnation);
    }

    #[test]
    fn cached_samples_do_not_count() {
        let history = vec![
            verified(1, 4, true),
            verified(5, 4, false),
            verified(9, 4, false),
        ];
        assert!(detect(&history, &history[2], 3, 8).is_none());
    }

    #[test]
    fn changing_failures_is_not_stagnation() {
        let history = vec![
            verified(1, 4, true),
            verified(5, 3, true),
            verified(9, 4, true),
        ];
        assert!(detect(&history, &history[2], 3, 8).is_none());
    }
}
