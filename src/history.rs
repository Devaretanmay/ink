use regex::Regex;
use serde_json::Value;
use std::collections::BTreeMap;

use crate::config::ErrorDetectionCfg;

pub struct ToolCallInfo {
    pub name: String,
    pub arguments: Value,
}

pub struct ToolResult {
    pub content: String,
}

pub fn pair_tool_calls(messages: &[Value]) -> Vec<(ToolCallInfo, ToolResult)> {
    let mut pairs = Vec::new();
    let mut pending_calls = BTreeMap::new();

    for msg in messages {
        if let Some(role) = msg.get("role").and_then(|r| r.as_str()) {
            if role == "assistant" {
                if let Some(tool_calls) = msg.get("tool_calls").and_then(|t| t.as_array()) {
                    for tc in tool_calls {
                        let id = tc
                            .get("id")
                            .and_then(|i| i.as_str())
                            .unwrap_or("")
                            .to_string();
                        if let Some(func) = tc.get("function") {
                            let name = func
                                .get("name")
                                .and_then(|n| n.as_str())
                                .unwrap_or("")
                                .to_string();
                            let args_str = func
                                .get("arguments")
                                .and_then(|a| a.as_str())
                                .unwrap_or("{}");
                            let arguments: Value =
                                serde_json::from_str(args_str).unwrap_or(Value::Null);

                            pending_calls.insert(id, ToolCallInfo { name, arguments });
                        }
                    }
                } else if let Some(content_arr) = msg.get("content").and_then(|c| c.as_array()) {
                    for block in content_arr {
                        if block.get("type").and_then(|t| t.as_str()) == Some("tool_use") {
                            let id = block
                                .get("id")
                                .and_then(|i| i.as_str())
                                .unwrap_or("")
                                .to_string();
                            let name = block
                                .get("name")
                                .and_then(|n| n.as_str())
                                .unwrap_or("")
                                .to_string();
                            let arguments = block.get("input").cloned().unwrap_or(Value::Null);

                            pending_calls.insert(id, ToolCallInfo { name, arguments });
                        }
                    }
                }
            } else if role == "tool" {
                let id = msg
                    .get("tool_call_id")
                    .and_then(|i| i.as_str())
                    .unwrap_or("");
                let content = msg
                    .get("content")
                    .and_then(|c| c.as_str())
                    .unwrap_or("")
                    .to_string();

                if let Some(info) = pending_calls.remove(id) {
                    pairs.push((info, ToolResult { content }));
                }
            } else if role == "user"
                && let Some(content_arr) = msg.get("content").and_then(|c| c.as_array())
            {
                for block in content_arr {
                    if block.get("type").and_then(|t| t.as_str()) == Some("tool_result") {
                        let id = block
                            .get("tool_use_id")
                            .and_then(|i| i.as_str())
                            .unwrap_or("");

                        let content_str = match block.get("content") {
                            Some(Value::String(s)) => s.clone(),
                            Some(other) => other.to_string(),
                            None => String::new(),
                        };

                        if let Some(info) = pending_calls.remove(id) {
                            pairs.push((
                                info,
                                ToolResult {
                                    content: content_str,
                                },
                            ));
                        }
                    }
                }
            }
        }
    }

    pairs
}

fn get_json_path_value<'a>(val: &'a Value, path: &str) -> Option<&'a Value> {
        let pointer_path = if let Some(stripped) = path.strip_prefix("$.") {
        format!("/{}", stripped.replace('.', "/"))
    } else {
        path.to_string()
    };
    val.pointer(&pointer_path)
}

pub fn is_error_response(result: &str, cfg: Option<&ErrorDetectionCfg>) -> bool {
    if let Some(cfg) = cfg {
        if let Ok(json_val) = serde_json::from_str::<Value>(result) {
            if let Some(path) = &cfg.json_path
                && get_json_path_value(&json_val, path).is_some()
            {
                return true;
            }

            if let Some(status_field) = &cfg.status_field
                && let Some(not_in) = &cfg.status_not_in
                && let Some(current) = get_json_path_value(&json_val, status_field)
                && let Some(status_code) = current.as_u64()
                && !not_in.contains(&(status_code as u16))
            {
                return true;
            }
        }

        if let Some(regex_str) = &cfg.regex
            && let Ok(re) = Regex::new(regex_str)
            && re.is_match(result)
        {
            return true;
        }
    }

    false
}

const MAX_TOOLS: usize = 256;
const SEQ_LEN: usize = 16;

pub struct ToolArena {
    names: Vec<String>,
}

impl ToolArena {
    pub fn new() -> Self {
        Self {
            names: Vec::with_capacity(MAX_TOOLS),
        }
    }

    pub fn get_or_insert(&mut self, name: &str) -> u16 {
        for (i, n) in self.names.iter().enumerate() {
            if n == name {
                return i as u16;
            }
        }
        if self.names.len() < MAX_TOOLS {
            self.names.push(name.to_string());
            (self.names.len() - 1) as u16
        } else {
            u16::MAX
        }
    }
}

pub struct HistoryTracker {
    pub call_history: Vec<(String, String)>,
    tool_arena: ToolArena,
    sequence_ring: [u16; SEQ_LEN],
    sequence_idx: usize,
    oscillation_cycles: usize,
}

impl Default for HistoryTracker {
    fn default() -> Self {
        Self::new()
    }
}

impl HistoryTracker {
    pub fn new() -> Self {
        Self {
            call_history: Vec::new(),
            tool_arena: ToolArena::new(),
            sequence_ring: [0; SEQ_LEN],
            sequence_idx: 0,
            oscillation_cycles: 0,
        }
    }

    fn check_argument_variance(&self, period: usize) -> bool {
        let len = self.call_history.len();
        if len < period * 2 {
            return true;
        }
        for i in 1..=period {
            let (_, args1) = &self.call_history[len - i];
            let (_, args2) = &self.call_history[len - i - period];
            if args1 != args2 {
                return true;
            }
        }
        false
    }

    pub fn check_loop(
        &mut self,
        tool: &str,
        args: &str,
        ignore_args: bool,
        max_repeats: usize,
        history_window: Option<usize>,
    ) -> Result<(), String> {
        let mut repeat_count = 1;
        let window_size = history_window
            .unwrap_or(max_repeats * 2)
            .max(max_repeats * 2);

        for (past_tool, past_args) in self.call_history.iter().rev().take(window_size) {
            if past_tool == tool && (ignore_args || past_args == args) {
                repeat_count += 1;
            }
        }

        if repeat_count >= max_repeats {
            return Err(format!("Agent appears to be looping on tool {}", tool));
        }

        let tool_id = self.tool_arena.get_or_insert(tool);
        self.sequence_ring[self.sequence_idx % SEQ_LEN] = tool_id;
        self.sequence_idx += 1;
        self.call_history.push((tool.to_string(), args.to_string()));

        if let Some(period) = self.detect_oscillation() {
            self.oscillation_cycles += 1;
            let changing = self.check_argument_variance(period);
            if !changing {
                return Err("Agent is stuck in an oscillation loop with identical arguments.".into());
            }
            if self.oscillation_cycles >= max_repeats {
                return Err(format!("Agent oscillated {} times without progress. Pivot required.", self.oscillation_cycles));
            }
        } else {
            self.oscillation_cycles = 0;
        }

        Ok(())
    }

    fn detect_oscillation(&self) -> Option<usize> {
        if self.sequence_idx < 4 {
            return None;
        }
        let max_len = self.sequence_idx.min(SEQ_LEN);
        let mut window = Vec::with_capacity(max_len);
        for i in 0..max_len {
            let idx = (self.sequence_idx - max_len + i) % SEQ_LEN;
            window.push(self.sequence_ring[idx]);
        }
        for period in 2..=(max_len / 2) {
            let mut repeating = true;
            for i in period..max_len {
                if window[i] != window[i - period] {
                    repeating = false;
                    break;
                }
            }
            if repeating {
                return Some(period);
            }
        }
        None
    }
}
