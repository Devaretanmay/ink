//! Local evidence collection. No tool execution, model calls or recovery side effects.
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::sync::LazyLock;

static ANSI_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\x1b\[[0-9;]*[a-zA-Z]").unwrap());
static UUID_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
        .unwrap()
});
static HASH_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b[0-9a-fA-F]{40,64}\b").unwrap());
static HEX_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b0x[0-9a-fA-F]{4,16}\b").unwrap());
static TIMESTAMP_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b")
        .unwrap()
});
static TMP_PATH_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"/(?:tmp|var/folders/[^\s/]+/[^\s/]+/[^\s/]+|private/var/folders/[^\s/]+/[^\s/]+/[^\s/]+|root/\.cache/[^\s/]+)/[^\s\x22':;]+").unwrap()
});
static PID_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\b(?:pid|PID|process|PROCESS)\s*[=:]\s*\d+\b").unwrap());
static PORT_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r":\b[3-6][0-9]{4}\b").unwrap());
static WS_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[ \t]+").unwrap());

/// Masks volatile tokens (ANSI escapes, UUIDs, git/docker hashes, hex addresses, timestamps, temp paths, PIDs, ephemeral ports)
/// to enable robust normalized repetition detection in 2026 agent execution traces.
pub fn mask_volatile_noise(input: &str) -> String {
    let s = ANSI_RE.replace_all(input, "");
    let s = UUID_RE.replace_all(&s, "<UUID>");
    let s = HASH_RE.replace_all(&s, "<HASH>");
    let s = HEX_RE.replace_all(&s, "<HEX>");
    let s = TIMESTAMP_RE.replace_all(&s, "<TIMESTAMP>");
    let s = TMP_PATH_RE.replace_all(&s, "<TMP_PATH>");
    let s = PID_RE.replace_all(&s, "pid=<PID>");
    let s = PORT_RE.replace_all(&s, ":<PORT>");
    let mut s = WS_RE.replace_all(&s, " ").trim().to_string();
    if s.len() > 256 {
        s.truncate(256);
    }
    s
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Action {
    pub name: String,
    pub fingerprint: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub normalized_fingerprint: Option<String>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Observation {
    pub success: Option<bool>,
    pub fingerprint: Option<String>,
    pub error_fingerprint: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub normalized_fingerprint: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Verification {
    /// Stable identity of the verifier definition, e.g. a particular test suite.
    pub scope: String,
    /// Unique identity of a freshly executed verification, not of its output.
    pub observation_id: String,
    pub failures: u64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Event {
    pub schema_version: u8,
    pub run_id: String,
    pub step: u64,
    pub action: Action,
    pub observation: Observation,
    pub verification: Option<Verification>,
    pub state_fingerprint: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct MonitorConfig {
    pub window: usize,
    pub repetitions: usize,
    pub stagnation_steps: u64,
    pub verification_samples: usize,
    pub normalize_actions: bool,
}

impl Default for MonitorConfig {
    fn default() -> Self {
        Self {
            window: 32,
            repetitions: 3,
            stagnation_steps: 8,
            verification_samples: 3,
            normalize_actions: true,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ProgressState {
    Healthy,
    Warning,
    Stalled,
    Regressing,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Reason {
    RepeatedActionResult,
    NormalizedRepetition,
    RepeatedError,
    StateStagnation,
    StateOscillation,
    Regression,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Evidence {
    pub reason: Reason,
    pub steps: Vec<u64>,
    pub detail: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Decision {
    pub schema_version: u8,
    pub run_id: String,
    pub step: u64,
    pub state: ProgressState,
    /// Heuristic severity, deliberately not a calibrated failure probability.
    pub score: f64,
    pub evidence: Vec<Evidence>,
    pub verified_progress: bool,
}

pub struct Monitor {
    run_id: String,
    config: MonitorConfig,
    history: VecDeque<Event>,
    last_step: Option<u64>,
    scope: Option<String>,
}

fn valid_token(value: &str) -> bool {
    !value.trim().is_empty() && value.len() <= 256
}

impl Monitor {
    pub fn new(run_id: String, config: MonitorConfig) -> Result<Self, String> {
        if !valid_token(&run_id) {
            return Err("run_id must contain 1..256 bytes".into());
        }
        if !(4..=4096).contains(&config.window)
            || !(2..=config.window).contains(&config.repetitions)
            || !(2..=config.window).contains(&config.verification_samples)
            || config.stagnation_steps == 0
        {
            return Err("invalid monitor bounds: window 4..4096; samples/repetitions 2..window; stagnation_steps > 0".into());
        }
        Ok(Self {
            run_id,
            config,
            history: VecDeque::new(),
            last_step: None,
            scope: None,
        })
    }

    pub fn observe(&mut self, mut event: Event) -> Result<Decision, String> {
        self.validate(&event)?;
        if self.config.normalize_actions {
            if event.action.normalized_fingerprint.is_none() {
                event.action.normalized_fingerprint =
                    Some(mask_volatile_noise(&event.action.fingerprint));
            }
            if event.observation.normalized_fingerprint.is_none()
                && let Some(fp) = &event.observation.fingerprint
            {
                event.observation.normalized_fingerprint = Some(mask_volatile_noise(fp));
            }
        }
        let mut verified_progress = false;
        let mut regression = None;
        if let Some(v) = &event.verification {
            if self.scope.as_ref() != Some(&v.scope) {
                self.history.clear();
                self.scope = Some(v.scope.clone());
            }
            let previous: Vec<_> = self
                .history
                .iter()
                .filter_map(|e| e.verification.as_ref().map(|v| (e.step, v)))
                .collect();
            if previous
                .iter()
                .any(|(_, old)| old.observation_id == v.observation_id)
            {
                // A cached observation is not another measurement of a plateau.
                event.verification = None;
            } else if let Some((step, best)) = previous.iter().min_by_key(|(_, old)| old.failures) {
                if v.failures < best.failures {
                    verified_progress = true;
                } else if v.failures > best.failures {
                    regression = Some(Evidence {
                        reason: Reason::Regression,
                        steps: vec![*step, event.step],
                        detail: format!(
                            "Same verifier: failures increased from {} to {}",
                            best.failures, v.failures
                        ),
                    });
                }
            }
        }
        if verified_progress {
            self.history.clear();
        }
        self.last_step = Some(event.step);
        self.history.push_back(event.clone());
        while self.history.len() > self.config.window {
            self.history.pop_front();
        }

        let mut evidence = Vec::new();
        let mut state = ProgressState::Healthy;
        if let Some(item) = regression {
            evidence.push(item);
            state = ProgressState::Regressing;
        }
        if !verified_progress {
            let mut repeated_exact = false;
            if let Some(output) = &event.observation.fingerprint {
                let steps: Vec<_> = self
                    .history
                    .iter()
                    .filter(|e| {
                        e.action.name == event.action.name
                            && e.action.fingerprint == event.action.fingerprint
                            && e.observation.fingerprint.as_ref() == Some(output)
                            && e.observation.success == event.observation.success
                            && e.state_fingerprint == event.state_fingerprint
                    })
                    .map(|e| e.step)
                    .collect();
                if steps.len() >= self.config.repetitions {
                    repeated_exact = true;
                    evidence.push(Evidence {
                        reason: Reason::RepeatedActionResult,
                        steps,
                        detail: "Same action, result and supplied state fingerprints recurred"
                            .into(),
                    });
                    if state != ProgressState::Regressing {
                        state = if event.observation.success == Some(false) {
                            ProgressState::Stalled
                        } else {
                            ProgressState::Warning
                        };
                    }
                }
            }
            if !repeated_exact
                && self.config.normalize_actions
                && let (Some(norm_act), Some(norm_obs)) = (
                    &event.action.normalized_fingerprint,
                    &event.observation.normalized_fingerprint,
                )
            {
                let steps: Vec<_> = self
                    .history
                    .iter()
                    .filter(|e| {
                        e.action.name == event.action.name
                            && e.action.normalized_fingerprint.as_ref() == Some(norm_act)
                            && e.observation.normalized_fingerprint.as_ref() == Some(norm_obs)
                            && e.observation.success == event.observation.success
                            && e.state_fingerprint == event.state_fingerprint
                    })
                    .map(|e| e.step)
                    .collect();
                if steps.len() >= self.config.repetitions {
                    evidence.push(Evidence {
                        reason: Reason::NormalizedRepetition,
                        steps,
                        detail: "Structurally identical action and result recurred after masking volatile noise".into(),
                    });
                    if state != ProgressState::Regressing {
                        state = if event.observation.success == Some(false) {
                            ProgressState::Stalled
                        } else {
                            ProgressState::Warning
                        };
                    }
                }
            }
            if event.observation.success == Some(false)
                && let Some(error) = &event.observation.error_fingerprint
            {
                let steps: Vec<_> = self
                    .history
                    .iter()
                    .filter(|e| {
                        e.observation.success == Some(false)
                            && e.observation.error_fingerprint.as_ref() == Some(error)
                    })
                    .map(|e| e.step)
                    .collect();
                if steps.len() >= self.config.repetitions {
                    evidence.push(Evidence {
                        reason: Reason::RepeatedError,
                        steps,
                        detail: "Same error fingerprint recurred across failed actions".into(),
                    });
                    if state != ProgressState::Regressing {
                        state = ProgressState::Stalled;
                    }
                }
            }
            let samples: Vec<_> = self
                .history
                .iter()
                .filter_map(|e| e.verification.as_ref().map(|v| (e.step, v)))
                .collect();
            // Stagnation requires a fresh current verifier, not a stale cached metric.
            if let Some(current) = &event.verification
                && current.failures > 0
                && samples.len() >= self.config.verification_samples
            {
                let first = samples[0];
                if event.step - first.0 >= self.config.stagnation_steps
                    && samples.iter().all(|(_, v)| v.failures == current.failures)
                {
                    evidence.push(Evidence {
                        reason: Reason::StateStagnation,
                        steps: samples.iter().map(|(s, _)| *s).collect(),
                        detail: format!(
                            "{} fresh verifications still report {} failures over {} steps",
                            samples.len(),
                            current.failures,
                            event.step - first.0
                        ),
                    });
                    if state != ProgressState::Regressing {
                        state = ProgressState::Stalled;
                    }
                }
            }
            // Require contiguous state observations; alternating tools alone is not oscillation.
            if self.history.len() >= 6 {
                let tail: Vec<_> = self.history.iter().rev().take(6).collect();
                if tail.iter().all(|e| e.state_fingerprint.is_some())
                    && tail[0].state_fingerprint != tail[1].state_fingerprint
                    && (2..6).all(|i| tail[i].state_fingerprint == tail[i % 2].state_fingerprint)
                {
                    evidence.push(Evidence {
                        reason: Reason::StateOscillation,
                        steps: tail.iter().rev().map(|e| e.step).collect(),
                        detail: "Supplied environment state alternated A/B for three cycles".into(),
                    });
                    // State changes may be legitimate. Without verifier/error evidence, warn only.
                    if state == ProgressState::Healthy {
                        state = ProgressState::Warning;
                    }
                }
            }
        }
        let score = match state {
            ProgressState::Healthy => 0.0,
            ProgressState::Warning => 0.4,
            ProgressState::Stalled => 0.8,
            ProgressState::Regressing => 0.9,
        };
        Ok(Decision {
            schema_version: 1,
            run_id: self.run_id.clone(),
            step: event.step,
            state,
            score,
            evidence,
            verified_progress,
        })
    }

    fn validate(&self, event: &Event) -> Result<(), String> {
        if event.schema_version != 1 {
            return Err("unsupported event schema_version".into());
        }
        if event.run_id != self.run_id {
            return Err("event belongs to another run".into());
        }
        if self.last_step.is_some_and(|step| event.step <= step) {
            return Err("steps must strictly increase".into());
        }
        if !valid_token(&event.action.name) || !valid_token(&event.action.fingerprint) {
            return Err("action name/fingerprint must contain 1..256 bytes".into());
        }
        for token in [
            &event.action.normalized_fingerprint,
            &event.observation.fingerprint,
            &event.observation.error_fingerprint,
            &event.observation.normalized_fingerprint,
            &event.state_fingerprint,
        ]
        .into_iter()
        .flatten()
        {
            if !valid_token(token) {
                return Err("fingerprints must contain 1..256 bytes".into());
            }
        }
        if event.observation.error_fingerprint.is_some() && event.observation.success != Some(false)
        {
            return Err("error_fingerprint requires success=false".into());
        }
        if let Some(v) = &event.verification {
            if !valid_token(&v.scope) || !valid_token(&v.observation_id) {
                return Err("verifier identifiers must contain 1..256 bytes".into());
            }
            if self
                .history
                .iter()
                .filter_map(|e| e.verification.as_ref())
                .any(|old| {
                    old.scope == v.scope
                        && old.observation_id == v.observation_id
                        && old.failures != v.failures
                })
            {
                return Err("cached verification identity has conflicting values".into());
            }
        }
        Ok(())
    }
}
