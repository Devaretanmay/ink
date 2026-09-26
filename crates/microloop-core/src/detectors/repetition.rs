//! Repetition detection: identical action/observation/state recurred.

use crate::event::{Evidence, Reason};
use crate::history::Record;

/// Exact repetition: same action, observation, outcome and state.
pub(crate) fn exact(current: &Record, history: &[Record], repetitions: usize) -> Option<Evidence> {
    if current.observation.is_empty() {
        return None;
    }
    let steps: Vec<u64> = history
        .iter()
        .filter(|record| {
            record.action == current.action
                && record.observation == current.observation
                && record.failed == current.failed
                && record.state_key == current.state_key
        })
        .map(|record| record.step)
        .collect();
    if steps.len() < repetitions {
        return None;
    }
    Some(Evidence {
        reason: Reason::RepeatedActionResult,
        steps,
        detail: "Same action, observation and supplied state recurred".into(),
    })
}

/// Normalized repetition: structurally identical after masking volatile tokens.
pub(crate) fn normalized(
    current: &Record,
    history: &[Record],
    repetitions: usize,
) -> Option<Evidence> {
    let action = current.norm_action.as_ref()?;
    let observation = current.norm_observation.as_ref()?;
    if action.is_empty() || observation.is_empty() {
        return None;
    }
    let steps: Vec<u64> = history
        .iter()
        .filter(|record| {
            record.norm_action.as_ref() == Some(action)
                && record.norm_observation.as_ref() == Some(observation)
                && record.failed == current.failed
                && record.state_key == current.state_key
        })
        .map(|record| record.step)
        .collect();
    if steps.len() < repetitions {
        return None;
    }
    Some(Evidence {
        reason: Reason::NormalizedRepetition,
        steps,
        detail:
            "Structurally identical action and observation recurred after masking volatile noise"
                .into(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event::Event;

    fn record(event: Event) -> Record {
        Record::from_event(&event, true)
    }

    #[test]
    fn exact_repetition_is_ignored_without_an_observation() {
        let empty = record(Event::new(1, "shell", ""));
        let history = vec![empty.clone(), empty.clone(), empty.clone()];
        assert!(exact(&history[2], &history, 3).is_none());
    }

    #[test]
    fn exact_repetition_reports_all_matching_steps() {
        let history: Vec<Record> = (1..=3)
            .map(|step| record(Event::new(step, "shell", "out")))
            .collect();
        let found = exact(&history[2], &history, 3).expect("repeated");
        assert_eq!(found.reason, Reason::RepeatedActionResult);
        assert_eq!(found.steps, vec![1, 2, 3]);
    }

    #[test]
    fn normalized_repetition_masks_volatile_tokens() {
        let history: Vec<Record> = (1..=3)
            .map(|step| {
                record(Event::new(
                    step,
                    "run",
                    format!(
                        "2026-09-2{step}T12:00:00Z id 550e8400-e29b-41d4-a716-44665544000{step}"
                    ),
                ))
            })
            .collect();
        assert!(exact(&history[2], &history, 3).is_none());
        let found = normalized(&history[2], &history, 3).expect("normalized");
        assert_eq!(found.reason, Reason::NormalizedRepetition);
    }
}
