//! Oscillation detection: supplied environment state alternates A/B.

use crate::event::{Evidence, Reason};
use crate::history::Record;

/// Detect three contiguous A/B state cycles with no intervening change.
/// State changes may be legitimate, so callers treat this as a warning only.
pub(crate) fn detect(history: &[Record]) -> Option<Evidence> {
    if history.len() < 6 {
        return None;
    }
    let tail = &history[history.len() - 6..];
    if tail.iter().any(|record| record.state_key.is_none()) {
        return None;
    }
    let states: Vec<&String> = tail
        .iter()
        .map(|record| record.state_key.as_ref().expect("checked above"))
        .collect();
    if states[5] == states[4] {
        return None;
    }
    if !(2..6).all(|i| states[i] == states[i - 2]) {
        return None;
    }
    Some(Evidence {
        reason: Reason::StateOscillation,
        steps: tail.iter().map(|record| record.step).collect(),
        detail: "Supplied environment state alternated A/B for three cycles".into(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event::Event;

    fn record(step: u64, state: Option<&str>) -> Record {
        let mut event = Event::new(step, format!("action-{step}"), "");
        event.state = state.map(|value| {
            [("state".to_string(), value.to_string())]
                .into_iter()
                .collect()
        });
        Record::from_event(&event, true)
    }

    #[test]
    fn alternating_state_is_detected() {
        let history: Vec<Record> = (0..6)
            .map(|index| record(index + 1, Some(if index % 2 == 0 { "A" } else { "B" })))
            .collect();
        assert!(detect(&history).is_some());
    }

    #[test]
    fn constant_or_missing_state_is_not_oscillation() {
        let constant: Vec<Record> = (1..=6).map(|step| record(step, Some("A"))).collect();
        assert!(detect(&constant).is_none());
        let missing: Vec<Record> = (1..=6).map(|step| record(step, None)).collect();
        assert!(detect(&missing).is_none());
    }
}
