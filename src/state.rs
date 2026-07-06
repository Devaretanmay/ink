

use crate::config::{MicroloopConfig, MicroloopDefaults, Sensitivity, ToolConfig, CostTier};
use crate::engine::RuleEngine;
use crate::history::HistoryTracker;

pub const ERROR_BUF_SIZE: usize = 1024;

pub struct MicroloopState {
    pub sensitivity: Sensitivity,
    pub max_repeats: usize,
    pub ignore_args: bool,
    pub history_window: Option<usize>,

    pub engine: RuleEngine,
    pub history: HistoryTracker,
    pub error_buffer: Vec<u8>,

    pub defaults: Option<MicroloopDefaults>,
    pub tools: Vec<ToolConfig>,
}

impl MicroloopState {
    pub fn new(yaml_str: &str) -> Result<Self, String> {
        let config = MicroloopConfig::from_yaml(yaml_str)?;

        let engine = RuleEngine::new(config.rules)?;
        let history = HistoryTracker::new();

        Ok(Self {
            sensitivity: config.sensitivity,
            max_repeats: config.max_repeats,
            ignore_args: config.ignore_args,
            history_window: config.history_window,
            engine,
            history,
            error_buffer: Vec::with_capacity(ERROR_BUF_SIZE),
            defaults: config.defaults,
            tools: config.tools,
        })
    }

    pub fn block_result(&self) -> u8 {
        match self.sensitivity {
            Sensitivity::Low => 1,
            Sensitivity::Default => 2,
            Sensitivity::High => 3,
        }
    }

    pub fn get_effective_threshold(&self, tool_name: &str) -> usize {
        let base = match self.sensitivity {
            Sensitivity::Low => (self.max_repeats as f32 * 1.5) as usize,
            Sensitivity::Default => self.max_repeats,
            Sensitivity::High => (self.max_repeats as f32 * 0.6) as usize,
        };

        if let Some(tool_cfg) = self.tools.iter().find(|t| t.name == tool_name) {
            let gate = &tool_cfg.trajectory_gate;
            if let Some(weight) = gate.cost_weight {
                    return (base as f32 / weight).max(1.0) as usize;
                }
                if let Some(ref tier) = gate.cost_tier {
                    return match tier {
                        CostTier::Low => base + 2,
                        CostTier::Medium => base,
                        CostTier::High => base.saturating_sub(1).max(1),
                    };
                }
            }
        
        base
    }

    pub fn set_error(&mut self, msg: &str) {
        self.error_buffer.clear();

        let max_len = (ERROR_BUF_SIZE - 1).min(msg.len());
        self.error_buffer
            .extend_from_slice(&msg.as_bytes()[..max_len]);
        self.error_buffer.push(0);
    }
}
