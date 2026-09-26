//! Policy returns instructions. The host, never this module, executes them.
use crate::monitor::{Decision, ProgressState};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct PolicyConfig {
    pub replan: bool,
    pub cooldown_steps: u64,
    pub max_replans: usize,
    pub stop_at_step: Option<u64>,
}
impl Default for PolicyConfig {
    fn default() -> Self {
        Self {
            replan: false,
            cooldown_steps: 8,
            max_replans: 2,
            stop_at_step: None,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum InterventionKind {
    Observe,
    Replan,
    Stop,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Intervention {
    pub kind: InterventionKind,
    pub feedback: Option<String>,
}

pub struct Policy {
    config: PolicyConfig,
    run_id: Option<String>,
    last_step: Option<u64>,
    last_replan: Option<u64>,
    replans: usize,
}

impl Policy {
    pub fn new(config: PolicyConfig) -> Result<Self, String> {
        if config.cooldown_steps == 0 {
            return Err("cooldown_steps must be positive".into());
        }
        Ok(Self {
            config,
            run_id: None,
            last_step: None,
            last_replan: None,
            replans: 0,
        })
    }
    pub fn apply(&mut self, decision: &Decision) -> Result<Intervention, String> {
        if decision.schema_version != 1
            || decision.run_id.is_empty()
            || self
                .run_id
                .as_ref()
                .is_some_and(|id| id != &decision.run_id)
            || self.last_step.is_some_and(|s| decision.step <= s)
        {
            return Err("policy requires ordered decisions from one run, schema version 1".into());
        }
        self.run_id = Some(decision.run_id.clone());
        self.last_step = Some(decision.step);
        if self.config.stop_at_step.is_some_and(|s| decision.step >= s) {
            return Ok(Intervention {
                kind: InterventionKind::Stop,
                feedback: Some("Host-configured step budget reached".into()),
            });
        }
        if self.config.replan
            && self.replans < self.config.max_replans
            && matches!(
                decision.state,
                ProgressState::Stalled | ProgressState::Regressing
            )
            && !decision.evidence.is_empty()
            && self
                .last_replan
                .is_none_or(|s| decision.step - s >= self.config.cooldown_steps)
        {
            self.last_replan = Some(decision.step);
            self.replans += 1;
            let mut feedback = String::from(
                "MICROLOOP RECOVERY SIGNAL\nObserved evidence (heuristic, not a root-cause diagnosis):\n",
            );
            for item in &decision.evidence {
                feedback.push_str(&format!(
                    "- {:?} at steps {:?}: {}\n",
                    item.reason, item.steps, item.detail
                ));
            }
            feedback.push_str("Re-evaluate the approach using this evidence. Avoid repeating failed actions unless new evidence supports them.");
            return Ok(Intervention {
                kind: InterventionKind::Replan,
                feedback: Some(feedback),
            });
        }
        Ok(Intervention {
            kind: InterventionKind::Observe,
            feedback: None,
        })
    }
}
