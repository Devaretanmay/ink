//! Recurrent error detection: the same error signature keeps coming back.

use crate::event::{Evidence, Reason};
use crate::history::Record;

/// Detect a recurring error signature across failed steps.
pub fn recurrent(current: &Record, history: &[Record], repetitions: usize) -> Option<Evidence> {
    let signature = current.error_sig.as_ref()?;
    let steps: Vec<u64> = history
        .iter()
        .filter(|record| record.failed && record.error_sig.as_ref() == Some(signature))
        .map(|record| record.step)
        .collect();
    if steps.len() < repetitions {
        return None;
    }
    Some(Evidence {
        reason: Reason::RepeatedError,
        steps,
        detail: "Same error signature recurred across failed actions".into(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event::Event;

    fn failed(step: u64, signature: &str) -> Record {
        let mut event = Event::new(step, format!("action-{step}"), "boom");
        event.metrics = Some([("exit_code".to_string(), 1.0)].into_iter().collect());
        event.metadata = Some(
            [("error".to_string(), signature.to_string())]
                .into_iter()
                .collect(),
        );
        Record::from_event(&event, true)
    }

    #[test]
    fn recurring_signature_is_detected() {
        let history = vec![failed(1, "E"), failed(2, "E"), failed(3, "E")];
        let found = recurrent(&history[2], &history, 3).expect("recurrent");
        assert_eq!(found.reason, Reason::RepeatedError);
    }

    #[test]
    fn distinct_signatures_do_not_recur() {
        let history = vec![failed(1, "E1"), failed(2, "E2"), failed(3, "E3")];
        assert!(recurrent(&history[2], &history, 3).is_none());
    }
}
