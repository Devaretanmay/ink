
use std::collections::{HashMap, HashSet};

use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct ToolPair {
    pub assistant_index: usize,
    pub response_index: usize,
}

pub fn tool_pair_indices(messages: &[Value]) -> Vec<ToolPair> {
    let mut announced: HashMap<String, usize> = HashMap::new();
    for (i, msg) in messages.iter().enumerate() {
        if msg.get("role").and_then(Value::as_str) != Some("assistant") {
            continue;
        }
        for id in collect_assistant_tool_call_ids(msg) {
            announced.insert(id, i);
        }
    }

    let mut pairs: Vec<ToolPair> = Vec::new();
    let mut seen: HashSet<(usize, usize)> = HashSet::new();
    for (i, msg) in messages.iter().enumerate() {
        let role = msg.get("role").and_then(Value::as_str);

        if role == Some("tool") {
            if let Some(tcid) = msg.get("tool_call_id").and_then(Value::as_str) {
                if let Some(&assistant_index) = announced.get(tcid) {
                    if seen.insert((assistant_index, i)) {
                        pairs.push(ToolPair {
                            assistant_index,
                            response_index: i,
                        });
                    }
                }
            }
        }

        if role == Some("user") {
            if let Some(blocks) = msg.get("content").and_then(Value::as_array) {
                for block in blocks {
                    if block.get("type").and_then(Value::as_str) == Some("tool_result") {
                        if let Some(tuid) = block.get("tool_use_id").and_then(Value::as_str) {
                            if let Some(&assistant_index) = announced.get(tuid) {
                                if seen.insert((assistant_index, i)) {
                                    pairs.push(ToolPair {
                                        assistant_index,
                                        response_index: i,
                                    });
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    pairs
}

fn collect_assistant_tool_call_ids(assistant: &Value) -> HashSet<String> {
    let mut ids: HashSet<String> = HashSet::new();

    if let Some(arr) = assistant.get("tool_calls").and_then(Value::as_array) {
        for tc in arr {
            if let Some(id) = tc.get("id").and_then(Value::as_str) {
                ids.insert(id.to_string());
            }
        }
    }

    if let Some(blocks) = assistant.get("content").and_then(Value::as_array) {
        for block in blocks {
            if block.get("type").and_then(Value::as_str) == Some("tool_use") {
                if let Some(id) = block.get("id").and_then(Value::as_str) {
                    ids.insert(id.to_string());
                }
            }
        }
    }

    ids
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn openai_pair_is_detected() {
        let msgs = vec![
            json!({"role": "user", "content": "go"}),
            json!({
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "type": "function",
                                 "function": {"name": "f", "arguments": "{}"}}]
            }),
            json!({"role": "tool", "tool_call_id": "call_1", "content": "result"}),
            json!({"role": "user", "content": "thanks"}),
        ];
        let pairs = tool_pair_indices(&msgs);
        assert_eq!(pairs.len(), 1);
        assert_eq!(pairs[0].assistant_index, 1);
        assert_eq!(pairs[0].response_index, 2);
    }

    #[test]
    fn anthropic_pair_is_detected() {
        let msgs = vec![
            json!({"role": "user", "content": "go"}),
            json!({
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "thinking..."},
                    {"type": "tool_use", "id": "tu_1", "name": "f", "input": {}}
                ]
            }),
            json!({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"}]
            }),
        ];
        let pairs = tool_pair_indices(&msgs);
        assert_eq!(pairs.len(), 1);
        assert_eq!(pairs[0].assistant_index, 1);
        assert_eq!(pairs[0].response_index, 2);
    }

    #[test]
    fn unmatched_tool_response_is_dropped() {
        let msgs = vec![
            json!({"role": "user", "content": "go"}),
            json!({
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_known", "function": {"name": "f"}}]
            }),
            json!({"role": "tool", "tool_call_id": "call_orphan", "content": "?"}),
        ];
        let pairs = tool_pair_indices(&msgs);
        assert!(pairs.is_empty());
    }

    #[test]
    fn multiple_anthropic_tool_results_in_one_user_message() {
        let msgs = vec![
            json!({
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu_a", "name": "f"},
                    {"type": "tool_use", "id": "tu_b", "name": "g"}
                ]
            }),
            json!({
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_a", "content": "a"},
                    {"type": "tool_result", "tool_use_id": "tu_b", "content": "b"}
                ]
            }),
        ];
        let pairs = tool_pair_indices(&msgs);
        assert_eq!(pairs.len(), 1);
        assert_eq!(pairs[0].assistant_index, 0);
        assert_eq!(pairs[0].response_index, 1);
    }

    #[test]
    fn empty_messages_yields_no_pairs() {
        assert!(tool_pair_indices(&[]).is_empty());
    }
}
