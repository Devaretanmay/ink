use async_stream::stream;
use axum::{
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{
        IntoResponse,
        sse::{Event, Sse},
    },
};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};
use std::convert::Infallible;
use std::sync::{Arc, Mutex};
use sha2::{Digest, Sha256};

pub struct RollingSummary {
    pub max_size: usize,
    pub buffers: HashMap<String, Vec<String>>,
}

impl RollingSummary {
    pub fn new(max_size: usize) -> Self {
        Self { max_size, buffers: HashMap::new() }
    }

    pub fn push(&mut self, session_id: &str, signature: String) {
        let buf = self.buffers.entry(session_id.to_string()).or_default();
        if buf.len() >= self.max_size {
            buf.remove(0);
        }
        buf.push(signature);
    }

    pub fn get_recent_text(&self, session_id: &str, limit: usize) -> String {
        if let Some(buf) = self.buffers.get(session_id) {
            let start = buf.len().saturating_sub(limit);
            buf[start..].join("\n")
        } else {
            String::new()
        }
    }
}

#[derive(Serialize)]
pub struct AnalyzePayload {
    pub session_id: String,
    pub tool_call: String,
    pub llm_error_response: String,
}

#[derive(Deserialize)]
struct SidecarResponse {
    loop_detected: bool,
}

#[derive(Clone)]
pub struct AppState {
    pub microloop_state: Arc<Mutex<microloop::state::MicroloopState>>,
    pub semantic_blocklist: std::sync::Arc<dyn crate::blocklist::BlocklistStore>,
    pub target_base_url: String,
    pub api_key: String,
    pub sidecar_url: String,
    pub http_client: Client,
    pub ccr_store: Arc<crate::ccr::CcrStore>,
    pub trajectory_summary: Arc<Mutex<RollingSummary>>,
}


pub async fn intercept_tool_calls(
    session_id: &str,
    request_body: &Value,
    response: &mut Value,
    app_state: &AppState,
) {
    let empty_vec = Vec::new();
    let messages = request_body
        .get("messages")
        .and_then(|m| m.as_array())
        .unwrap_or(&empty_vec);
    let prior_outcomes = microloop::history::pair_tool_calls(messages);
    
    if !prior_outcomes.is_empty() {
        let mut ts = app_state.trajectory_summary.lock().unwrap();
        ts.buffers.insert(session_id.to_string(), Vec::new());
        for (call, res) in prior_outcomes.iter().rev().take(ts.max_size).rev() {
            let mut tr = res.content.clone();
            if tr.len() > 100 { tr.truncate(100); tr.push_str("..."); }
            let sig = format!("{}({}) -> {}", call.name, call.arguments, tr);
            ts.push(session_id, sig);
        }
    }

    let llm_error_response = response
        .pointer("/choices/0/message/content")
        .and_then(|c| c.as_str())
        .unwrap_or("")
        .to_string();

    let mut parsed_tool_calls = Vec::new();
    let mut is_anthropic = false;

    if let Some(choices) = response.get_mut("choices").and_then(|c| c.as_array_mut()) {
        if let Some(first_choice) = choices.first_mut()
            && let Some(message) = first_choice.get_mut("message")
            && let Some(tool_calls) = message.get("tool_calls").and_then(|tc| tc.as_array())
        {
            for tc in tool_calls {
                if let Some(function) = tc.get("function") {
                    let name = function
                        .get("name")
                        .and_then(|n| n.as_str())
                        .unwrap_or("")
                        .to_string();
                    let arguments_str = function
                        .get("arguments")
                        .and_then(|a| a.as_str())
                        .unwrap_or("{}")
                        .to_string();
                    let arguments_val: Value =
                        serde_json::from_str(&arguments_str).unwrap_or(json!({}));
                    parsed_tool_calls.push((name, arguments_str, arguments_val));
                }
            }
        }
    } else if response.get("type").and_then(|t| t.as_str()) == Some("message") {
        is_anthropic = true;
        if let Some(content) = response.get("content").and_then(|c| c.as_array()) {
            for block in content {
                if block.get("type").and_then(|t| t.as_str()) == Some("tool_use") {
                    let name = block
                        .get("name")
                        .and_then(|n| n.as_str())
                        .unwrap_or("")
                        .to_string();
                    let arguments_val = block.get("input").cloned().unwrap_or(json!({}));
                    let arguments_str =
                        serde_json::to_string(&arguments_val).unwrap_or_else(|_| "{}".to_string());
                    parsed_tool_calls.push((name, arguments_str, arguments_val));
                }
            }
        }
    }

    if parsed_tool_calls.is_empty() {
        return;
    }

    for (name, arguments_str, arguments_val) in parsed_tool_calls {
        
        if app_state.semantic_blocklist.is_blocked(session_id, &name).await.unwrap_or(false) {
            eprintln!("Semantic Loop Blocked: tool {}", name);
            block_response(response, is_anthropic, format!("Semantic loop detected on tool '{}'", name));
            return;
        }
        
        let analyze_payload = AnalyzePayload {
            session_id: session_id.to_string(),
            tool_call: format!("{}({})", name, arguments_str),
            llm_error_response: llm_error_response.clone(),
        };

        if !app_state.sidecar_url.is_empty() {
            let url = format!("{}/analyze", app_state.sidecar_url);
            let loop_detected = async {
                let resp = app_state.http_client.post(&url).json(&analyze_payload).send().await.ok()?;
                let result = resp.json::<SidecarResponse>().await.ok()?;
                Some(result.loop_detected)
            }.await.unwrap_or(false);

            if loop_detected {
                eprintln!("Semantic Loop Blocked (sync): tool {}", name);
                block_response(response, is_anthropic, format!("Semantic loop detected on tool '{}'", name));
                return;
            }
        }

        let mut state = app_state.microloop_state.lock().unwrap();

        let empty_volatile = Vec::new();
        let mut volatile_fields = &empty_volatile;
        let mut error_cfg = None;
        let mut max_repeats = state
            .defaults
            .as_ref()
            .map(|d| d.trajectory_gate.max_repeats)
            .unwrap_or(state.max_repeats);
        let mut count_mode = state
            .defaults
            .as_ref()
            .map(|d| d.trajectory_gate.count_mode.clone())
            .unwrap_or(microloop::config::CountMode::All);

        for t_cfg in &state.tools {
            if t_cfg.name == name {
                if let Some(mr) = t_cfg.trajectory_gate.max_repeats {
                    max_repeats = mr;
                }
                if let Some(cm) = &t_cfg.trajectory_gate.count_mode {
                    count_mode = cm.clone();
                }
                volatile_fields = &t_cfg.trajectory_gate.volatile_fields;
                error_cfg = t_cfg.trajectory_gate.error_detection.as_ref();
                break;
            }
        }

        let mut current_args = arguments_val.clone();
        microloop::canonical::strip_volatile_fields(&mut current_args, volatile_fields);

        let mut match_count = 0;
        let mut error_count = 0;
        let mut auto_inferred_volatile = HashSet::new();
        let mut field_diff_counts: HashMap<String, usize> = HashMap::new();

        for (prior_call, prior_res) in &prior_outcomes {
            if prior_call.name == name {
                let mut prior_args = prior_call.arguments.clone();
                microloop::canonical::strip_volatile_fields(&mut prior_args, volatile_fields);
                if prior_args == current_args {
                    match_count += 1;
                    if microloop::history::is_error_response(&prior_res.content, error_cfg) {
                        error_count += 1;
                    }
                } else if let (Value::Object(p_map), Value::Object(c_map)) = (&prior_args, &current_args) {
                    let mut diffs = Vec::new();
                    for (k, v) in c_map {
                        if p_map.get(k) != Some(v) { diffs.push(k.clone()); }
                    }
                    for (k, _) in p_map {
                        if !c_map.contains_key(k) && !diffs.contains(k) { diffs.push(k.clone()); }
                    }
                    for diff in &diffs {
                        *field_diff_counts.entry(diff.clone()).or_insert(0) += 1;
                    }
                    if diffs.len() == 1 {
                        auto_inferred_volatile.insert(diffs[0].clone());
                    }
                }
            }
        }

        if !auto_inferred_volatile.is_empty() {
            let min_occurrences = 2;
            auto_inferred_volatile.retain(|field| {
                let count = field_diff_counts.get(field).copied().unwrap_or(0);
                count >= min_occurrences
            });
        }

        if !auto_inferred_volatile.is_empty() {
            let mut combined_volatile = volatile_fields.clone();
            combined_volatile.extend(auto_inferred_volatile);
            
            match_count = 0;
            error_count = 0;
            let mut new_current_args = arguments_val.clone();
            microloop::canonical::strip_volatile_fields(&mut new_current_args, &combined_volatile);
            
            for (prior_call, prior_res) in &prior_outcomes {
                if prior_call.name == name {
                    let mut prior_args = prior_call.arguments.clone();
                    microloop::canonical::strip_volatile_fields(&mut prior_args, &combined_volatile);
                    if prior_args == new_current_args {
                        match_count += 1;
                        if microloop::history::is_error_response(&prior_res.content, error_cfg) {
                            error_count += 1;
                        }
                    }
                }
            }
            eprintln!("Volatile Auto-Inference activated. Masking fields: {:?}", combined_volatile);
        }

        let count = match count_mode {
            microloop::config::CountMode::All => match_count,
            microloop::config::CountMode::ErrorsOnly => error_count,
        };

        if error_count > 0 {
            max_repeats = max_repeats.saturating_sub(1).max(2);
        }

        let res = if count + 1 >= max_repeats {
            2
        } else {
            match state.engine.validate(&arguments_str) {
                Ok(_) => 0,
                Err(msg) => {
                    state.set_error(msg);
                    state.block_result()
                }
            }
        };

        let label = match res {
            0 => "ALLOW",
            1 => "WARN",
            2 => "BLOCK_RETRY",
            3 => "BLOCK_HALT",
            _ => "UNKNOWN",
        };
        eprintln!(
            "{}",
            json!( {
                "event": "microloop_verify",
                "tool": name,
                "verdict": res,
                "label": label,
                "stateless_match_count": match_count,
            })
        );

        if res != 0 {
            let mut err_str = if count + 1 >= max_repeats {
                format!(
                    "Trajectory blocked by stateless history. Seen {} times, {} errors. Limit: {}",
                    match_count, error_count, max_repeats
                )
            } else {
                std::str::from_utf8(&state.error_buffer)
                    .unwrap_or("Unknown error")
                    .trim_end_matches('\0')
                    .to_string()
            };

            let ts = app_state.trajectory_summary.lock().unwrap();
            let summary = ts.get_recent_text(session_id, 3);
            if !summary.is_empty() {
                err_str = format!("CRITICAL: You are in a loop. Pivot your strategy immediately.\nSummary of your recent failed trajectory:\n{}\n\nOriginal Reason: {}", summary, err_str);
            }

            block_response(response, is_anthropic, err_str);
            return;
        }
    }
}

fn block_response(response: &mut Value, is_anthropic: bool, err_str: String) {
    if is_anthropic {
        if let Value::Object(resp_map) = response {
            resp_map.insert("stop_reason".to_string(), json!("end_turn"));
            resp_map.insert(
                "content".to_string(),
                json!([{
                    "type": "text",
                    "text": format!("SYSTEM INTERCEPT: Microloop blocked this action because: {}", err_str)
                }])
            );
        }
    } else {
        if let Some(choices) = response.get_mut("choices").and_then(|c| c.as_array_mut())
            && let Some(first_choice) = choices.first_mut()
            && let Some(Value::Object(msg_map)) = first_choice.get_mut("message")
        {
            msg_map.remove("tool_calls");
            msg_map.insert(
                "content".to_string(),
                json!(format!(
                    "SYSTEM INTERCEPT: Microloop blocked this action because: {}",
                    err_str
                )),
            );
        }
    }
}

pub async fn handle_proxy_request(
    State(state): State<AppState>,
    headers: HeaderMap,
    axum::extract::OriginalUri(uri): axum::extract::OriginalUri,
    axum::Json(mut body): axum::Json<Value>,
) -> impl IntoResponse {
    let client = Client::new();
    let path = uri.path();

    let upstream_path = if path == "/v1/chat/completions" {
        "/chat/completions"
    } else if path == "/v1/messages" {
        "/messages"
    } else {
        path
    };

    let url = format!(
        "{}/v1{}",
        state.target_base_url.trim_end_matches('/'),
        upstream_path
    );

    let stream_requested = body
        .get("stream")
        .and_then(|s| s.as_bool())
        .unwrap_or(false);

    if let Value::Object(ref mut map) = body {
            map.insert("stream".to_string(), json!(false));
            
            if let Some(Value::Array(messages)) = map.get_mut("messages") {
                for msg in messages.iter_mut() {
                    if let Some(msg_map) = msg.as_object_mut() {
                        if msg_map.get("role").and_then(|r| r.as_str()) == Some("tool") {
                            if let Some(content_val) = msg_map.get_mut("content") {
                                if let Some(content_str) = content_val.as_str() {
                                    let compressed = microloop_compress::route_and_compress(content_str);
                                    
                                    let mut hasher = Sha256::new();
                                    hasher.update(content_str.as_bytes());
                                    let original_hash = format!("{:x}", hasher.finalize());
                                    
                                    if let Err(e) = state.ccr_store.insert(&original_hash, content_str) {
                                        eprintln!("CCR Insert Error: {}", e);
                                    }
                                    
                                    *content_val = json!(format!(
                                        "{}\n[Original data truncated. Call microloop_retrieve with hash: {}]",
                                        compressed, original_hash
                                    ));
                                }
                            }
                        }
                    }
                }
            }
        }

    let mut upstream_headers = reqwest::header::HeaderMap::new();
    upstream_headers.insert("Content-Type", "application/json".parse().unwrap());
    if let Some(auth) = headers.get("authorization") {
        upstream_headers.insert("Authorization", auth.clone());
    } else if let Some(anthropic_key) = headers.get("x-api-key") {
        upstream_headers.insert("x-api-key", anthropic_key.clone());
    } else if !state.api_key.is_empty() {
        if path == "/v1/messages" {
            upstream_headers.insert("x-api-key", state.api_key.parse().unwrap());
            upstream_headers.insert("anthropic-version", "2023-06-01".parse().unwrap());
        } else {
            upstream_headers.insert(
                "Authorization",
                format!("Bearer {}", state.api_key).parse().unwrap(),
            );
        }
    }

    let resp = match client
        .post(&url)
        .headers(upstream_headers)
        .json(&body)
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            return (StatusCode::BAD_GATEWAY, format!("Upstream error: {}", e)).into_response();
        }
    };

    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return (status, body).into_response();
    }

    let mut response_json: Value = match resp.json().await {
        Ok(j) => j,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Invalid JSON: {}", e),
            )
                .into_response();
        }
    };

    let session_id = headers.get("x-session-id")
        .and_then(|h| h.to_str().ok())
        .unwrap_or("default_session")
        .to_string();

    intercept_tool_calls(&session_id, &body, &mut response_json, &state).await;

    if stream_requested {
        let sse_stream = stream! {
            let mut chunk1 = response_json.clone();
            if let Some(choices) = chunk1.get_mut("choices").and_then(|c| c.as_array_mut())
                && let Some(c) = choices.first_mut()
                    && let Value::Object(m) = c {
                        let msg = m.remove("message").unwrap_or(json!({}));
                        m.insert("delta".to_string(), msg);
                    }
            yield Ok::<_, Infallible>(Event::default().json_data(chunk1).unwrap());
            yield Ok::<_, Infallible>(Event::default().data("[DONE]"));
        };
        Sse::new(sse_stream).into_response()
    } else {
        axum::Json(response_json).into_response()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};
    use crate::blocklist::InMemoryBlocklist;

    #[tokio::test]
    async fn test_volatile_auto_inference() {
        let state = microloop::state::MicroloopState::new("max_repeats: 3\n").unwrap();
        let app_state = AppState {
            microloop_state: Arc::new(Mutex::new(state)),
            semantic_blocklist: Arc::new(InMemoryBlocklist::new()),
            target_base_url: "".to_string(),
            api_key: "".to_string(),
            sidecar_url: "".to_string(),
            http_client: reqwest::Client::new(),
            ccr_store: Arc::new(crate::ccr::CcrStore::new(":memory:").unwrap()),
            trajectory_summary: Arc::new(Mutex::new(RollingSummary::new(5))),
        };

        let request_body = json!({
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "search", "arguments": "{\"query\": \"hello\", \"req_id\": 1}"}}
                    ]
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "Result 1"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call_2", "type": "function", "function": {"name": "search", "arguments": "{\"query\": \"hello\", \"req_id\": 2}"}}
                    ]
                },
                {"role": "tool", "tool_call_id": "call_2", "content": "Result 2"}
            ]
        });

        let mut response = json!({
            "choices": [{
                "message": {
                    "tool_calls": [
                        {"function": {"name": "search", "arguments": "{\"query\": \"hello\", \"req_id\": 3}"}}
                    ]
                }
            }]
        });

        
        intercept_tool_calls("session_1", &request_body, &mut response, &app_state).await;

        let content = response["choices"][0]["message"]["content"].as_str().unwrap_or("");
        assert!(content.contains("SYSTEM INTERCEPT"), "Proxy did not intercept! Content: {}", content);
    }

    #[tokio::test]
    async fn test_no_false_positive_when_query_changes() {
        let state = microloop::state::MicroloopState::new("max_repeats: 3\n").unwrap();
        let app_state = AppState {
            microloop_state: Arc::new(Mutex::new(state)),
            semantic_blocklist: Arc::new(InMemoryBlocklist::new()),
            target_base_url: "".to_string(),
            api_key: "".to_string(),
            sidecar_url: "".to_string(),
            http_client: reqwest::Client::new(),
            ccr_store: Arc::new(crate::ccr::CcrStore::new(":memory:").unwrap()),
            trajectory_summary: Arc::new(Mutex::new(RollingSummary::new(5))),
        };

        let request_body = json!({
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "search", "arguments": "{\"query\": \"hello\", \"req_id\": 1}"}}
                    ]
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "Result 1"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call_2", "type": "function", "function": {"name": "search", "arguments": "{\"query\": \"world\", \"req_id\": 2}"}}
                    ]
                },
                {"role": "tool", "tool_call_id": "call_2", "content": "Result 2"}
            ]
        });

        let mut response = json!({
            "choices": [{
                "message": {
                    "tool_calls": [
                        {"function": {"name": "search", "arguments": "{\"query\": \"foo\", \"req_id\": 3}"}}
                    ]
                }
            }]
        });

        intercept_tool_calls("session_1", &request_body, &mut response, &app_state).await;

        let tool_calls = response["choices"][0]["message"]["tool_calls"].as_array();
        assert!(tool_calls.is_some(), "Tool calls were incorrectly removed!");
        assert!(!tool_calls.unwrap().is_empty(), "Tool call was incorrectly blocked!");
    }

    #[tokio::test]
    async fn test_blocklist_trait_in_memory() {
        use crate::blocklist::BlocklistStore;
        let blocklist = InMemoryBlocklist::new();
        
        assert!(!blocklist.is_blocked("session_1", "tool_a").await.unwrap());
        
        blocklist.add_block("session_1", "tool_a").await.unwrap();
        assert!(blocklist.is_blocked("session_1", "tool_a").await.unwrap());
        
        assert!(!blocklist.is_blocked("session_2", "tool_a").await.unwrap());
        
        assert!(!blocklist.is_blocked("session_1", "tool_b").await.unwrap());
    }

    #[tokio::test]
    async fn test_auto_inference_requires_multiple_diffs() {
        let state = microloop::state::MicroloopState::new("max_repeats: 3\n").unwrap();
        let app_state = AppState {
            microloop_state: Arc::new(Mutex::new(state)),
            semantic_blocklist: Arc::new(InMemoryBlocklist::new()),
            target_base_url: "".to_string(),
            api_key: "".to_string(),
            sidecar_url: "".to_string(),
            http_client: reqwest::Client::new(),
            ccr_store: Arc::new(crate::ccr::CcrStore::new(":memory:").unwrap()),
            trajectory_summary: Arc::new(Mutex::new(RollingSummary::new(5))),
        };

        let request_body = json!({
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "search", "arguments": "{\"query\": \"python\", \"req_id\": 1}"}}
                    ]
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "Result 1"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call_2", "type": "function", "function": {"name": "search", "arguments": "{\"query\": \"python\", \"req_id\": 2}"}}
                    ]
                },
                {"role": "tool", "tool_call_id": "call_2", "content": "Result 2"}
            ]
        });

        let mut response = json!({
            "choices": [{
                "message": {
                    "tool_calls": [
                        {"function": {"name": "search", "arguments": "{\"query\": \"rust\", \"req_id\": 3}"}}
                    ]
                }
            }]
        });

        intercept_tool_calls("session_1", &request_body, &mut response, &app_state).await;

        let tool_calls = response["choices"][0]["message"]["tool_calls"].as_array();
        assert!(tool_calls.is_some(), "Tool calls should not be removed for non-loop!");
        assert!(!tool_calls.unwrap().is_empty(), "Tool call should not be blocked for genuine change!");
    }

    #[tokio::test]
    async fn test_auto_inference_triggers_on_consistent_field() {
        let state = microloop::state::MicroloopState::new("max_repeats: 3\n").unwrap();
        let app_state = AppState {
            microloop_state: Arc::new(Mutex::new(state)),
            semantic_blocklist: Arc::new(InMemoryBlocklist::new()),
            target_base_url: "".to_string(),
            api_key: "".to_string(),
            sidecar_url: "".to_string(),
            http_client: reqwest::Client::new(),
            ccr_store: Arc::new(crate::ccr::CcrStore::new(":memory:").unwrap()),
            trajectory_summary: Arc::new(Mutex::new(RollingSummary::new(5))),
        };

        let request_body = json!({
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "search", "arguments": "{\"query\": \"python\", \"req_id\": 1}"}}
                    ]
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "Result 1"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call_2", "type": "function", "function": {"name": "search", "arguments": "{\"query\": \"python\", \"req_id\": 2}"}}
                    ]
                },
                {"role": "tool", "tool_call_id": "call_2", "content": "Result 2"}
            ]
        });

        let mut response = json!({
            "choices": [{
                "message": {
                    "tool_calls": [
                        {"function": {"name": "search", "arguments": "{\"query\": \"python\", \"req_id\": 3}"}}
                    ]
                }
            }]
        });

        intercept_tool_calls("session_1", &request_body, &mut response, &app_state).await;

        let content = response["choices"][0]["message"]["content"].as_str().unwrap_or("");
        assert!(content.contains("SYSTEM INTERCEPT"), "Should auto-infer req_id as volatile and block loop!");
    }

    #[tokio::test]
    async fn test_adaptive_thresholding_reduces_repeats() {
        let state = microloop::state::MicroloopState::new(
            "max_repeats: 3\nrules:\n  - type: regex\n    pattern: \"error\"\n"
        ).unwrap();
        let app_state = AppState {
            microloop_state: Arc::new(Mutex::new(state)),
            semantic_blocklist: Arc::new(InMemoryBlocklist::new()),
            target_base_url: "".to_string(),
            api_key: "".to_string(),
            sidecar_url: "".to_string(),
            http_client: reqwest::Client::new(),
            ccr_store: Arc::new(crate::ccr::CcrStore::new(":memory:").unwrap()),
            trajectory_summary: Arc::new(Mutex::new(RollingSummary::new(5))),
        };

        let request_body = json!({
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "api_call", "arguments": "{\"endpoint\": \"/fail\"}"}}
                    ]
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "{\"error\": \"failed\"}"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call_2", "type": "function", "function": {"name": "api_call", "arguments": "{\"endpoint\": \"/fail\"}"}}
                    ]
                },
                {"role": "tool", "tool_call_id": "call_2", "content": "{\"error\": \"failed\"}"}
            ]
        });

        let mut response = json!({
            "choices": [{
                "message": {
                    "tool_calls": [
                        {"function": {"name": "api_call", "arguments": "{\"endpoint\": \"/fail\"}"}}
                    ]
                }
            }]
        });

        intercept_tool_calls("session_1", &request_body, &mut response, &app_state).await;

        let content = response["choices"][0]["message"]["content"].as_str().unwrap_or("");
        assert!(content.contains("SYSTEM INTERCEPT") || content.contains("Trajectory blocked"), 
            "Should block with adaptive threshold when errors present. Content: {}", content);
    }
}
