//! Progress engine: runs the detectors and synthesizes one progress state.

use crate::detectors::{error, oscillation, repetition, stagnation, verification};
use crate::event::{Event, Evidence, MonitorConfig, ProgressState, Reason};
use crate::history::{failures_from, History, Record};

/// Detection result for a single step, before policy is applied.
#[derive(Clone, Debug)]
pub struct Outcome {
    pub step: u64,
    pub status: ProgressState,
    pub reasons: Vec<Reason>,
    pub evidence: Vec<Evidence>,
    pub verified_progress: bool,
}

/// Deterministic progress runtime. Holds the bounded trajectory history and the
/// verification scope. It never executes agent actions or performs I/O.
pub struct ProgressEngine {
    config: MonitorConfig,
    history: History,
    scope: Option<String>,
    last_step: Option<u64>,
}

impl ProgressEngine {
    pub fn new(config: MonitorConfig) -> Result<Self, String> {
        config.validate()?;
        Ok(Self {
            history: History::new(config.window),
            config,
            scope: None,
            last_step: None,
        })
    }

    pub fn config(&self) -> &MonitorConfig {
        &self.config
    }

    /// Observe one step and classify the trajectory so far.
    pub fn observe(&mut self, event: Event) -> Result<Outcome, String> {
        self.validate(&event)?;

        let mut record = Record::from_event(&event, self.config.normalization);
        let mut evidence: Vec<Evidence> = Vec::new();
        let mut reasons: Vec<Reason> = Vec::new();
        let mut status = ProgressState::Healthy;
        let mut verified_progress = false;

        // Verifier handling: scope switches reset history; only fresh samples
        // count; a lower failure count is verified progress.
        if let (Some(failures), Some(verifier)) = (record.failures, record.verifier.clone()) {
            if self.scope.as_deref() != Some(verifier.as_str()) {
                self.history.clear();
                self.scope = Some(verifier.clone());
            }
            let cached = record.verification_id.is_some()
                && self.history.iter().any(|previous| {
                    previous.verifier.as_deref() == Some(verifier.as_str())
                        && previous.verification_id == record.verification_id
                });
            if cached {
                record.fresh_verification = false;
            } else {
                record.fresh_verification = true;
                let previous: Vec<(u64, u64)> = self
                    .history
                    .iter()
                    .filter(|previous| {
                        previous.fresh_verification
                            && previous.verifier.as_deref() == Some(verifier.as_str())
                    })
                    .filter_map(|previous| previous.failures.map(|f| (previous.step, f)))
                    .collect();
                let (progress, regression) = verification::compare(&previous, failures);
                if progress {
                    verified_progress = true;
                }
                if let Some(regression) = regression {
                    reasons.push(Reason::Regression);
                    evidence.push(Evidence {
                        reason: Reason::Regression,
                        steps: vec![regression.from_step, event.step],
                        detail: format!(
                            "Same verifier: failures increased from {} to {}",
                            regression.from_failures, failures
                        ),
                    });
                    status = ProgressState::Regressing;
                }
            }
        }

        if verified_progress {
            self.history.clear();
        }
        self.last_step = Some(event.step);
        self.history.push(record);

        if !verified_progress {
            let records = self.history.as_slice();
            let current = records.last().expect("just pushed");

            if let Some(found) = repetition::exact(current, records, self.config.repetitions) {
                status = repetition_status(current);
                reasons.push(found.reason);
                evidence.push(found);
            } else if let Some(found) =
                repetition::normalized(current, records, self.config.repetitions)
            {
                status = repetition_status(current);
                reasons.push(found.reason);
                evidence.push(found);
            }

            if let Some(found) = error::recurrent(current, records, self.config.repetitions) {
                if status != ProgressState::Regressing {
                    status = ProgressState::Stalled;
                }
                reasons.push(found.reason);
                evidence.push(found);
            }

            if let Some(found) = stagnation::detect(
                records,
                current,
                self.config.verification_samples,
                self.config.stagnation_steps,
            ) {
                if status != ProgressState::Regressing {
                    status = ProgressState::Stalled;
                }
                reasons.push(found.reason);
                evidence.push(found);
            }

            if let Some(found) = oscillation::detect(records) {
                if status == ProgressState::Healthy {
                    status = ProgressState::Warning;
                }
                reasons.push(found.reason);
                evidence.push(found);
            }
        }

        Ok(Outcome {
            step: event.step,
            status,
            reasons,
            evidence,
            verified_progress,
        })
    }

    fn validate(&self, event: &Event) -> Result<(), String> {
        if event.action.trim().is_empty() {
            return Err("action must not be empty".into());
        }
        if event.action.len() > 4096 {
            return Err("action must be at most 4096 bytes".into());
        }
        if self
            .last_step
            .is_some_and(|previous| event.step <= previous)
        {
            return Err("steps must strictly increase".into());
        }
        if let (Some(failures), Some(verifier), Some(verification_id)) = (
            failures_from(event),
            event.metadata.as_ref().and_then(|map| map.get("verifier")),
            event
                .metadata
                .as_ref()
                .and_then(|map| map.get("verification_id")),
        ) {
            let conflict = self.history.iter().any(|previous| {
                previous.verifier.as_deref() == Some(verifier.as_str())
                    && previous.verification_id.as_deref() == Some(verification_id.as_str())
                    && previous.failures.is_some()
                    && previous.failures != Some(failures)
            });
            if conflict {
                return Err("cached verification identity has conflicting values".into());
            }
        }
        Ok(())
    }
}

fn repetition_status(current: &Record) -> ProgressState {
    if current.failed {
        ProgressState::Stalled
    } else {
        ProgressState::Warning
    }
}
