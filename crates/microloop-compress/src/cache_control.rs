
use serde_json::Value;

const CACHE_TTL_1H: &str = "1h";

const CACHE_TTL_5M: &str = "5m";

pub fn compute_frozen_count(parsed: &Value) -> usize {
    let mut highest_message_index: Option<usize> = None;

    walk_messages(parsed, &mut highest_message_index);

    walk_system(parsed);

    walk_tools(parsed);

    highest_message_index.map(|i| i + 1).unwrap_or(0)
}

fn walk_messages(parsed: &Value, highest_message_index: &mut Option<usize>) {
    let Some(messages) = parsed.get("messages").and_then(Value::as_array) else {
        return;
    };

    let mut ttl_walk = TtlOrderingWalk::new();

    for (i, message) in messages.iter().enumerate() {
        let Some(content) = message.get("content") else {
            continue;
        };
        let Some(blocks) = content.as_array() else {
            continue;
        };
        for block in blocks {
            if let Some(marker) = block.get("cache_control") {
                let ttl = extract_ttl(marker);
                tracing::debug!(
                    field = "messages",
                    message_index = i,
                    ttl = ttl.as_deref().unwrap_or("default"),
                    "cache_control marker found"
                );
                ttl_walk.observe(ttl.as_deref());
                *highest_message_index = Some(match highest_message_index {
                    Some(prev) => (*prev).max(i),
                    None => i,
                });
            }
        }
    }

    ttl_walk.warn_if_violated("messages");
}

fn walk_system(parsed: &Value) {
    let Some(system) = parsed.get("system") else {
        return;
    };
    let Some(blocks) = system.as_array() else {
        return;
    };
    let mut ttl_walk = TtlOrderingWalk::new();
    for block in blocks {
        if let Some(marker) = block.get("cache_control") {
            let ttl = extract_ttl(marker);
            tracing::debug!(
                field = "system",
                ttl = ttl.as_deref().unwrap_or("default"),
                "cache_control marker found"
            );
            ttl_walk.observe(ttl.as_deref());
        }
    }
    ttl_walk.warn_if_violated("system");
}

fn walk_tools(parsed: &Value) {
    let Some(tools) = parsed.get("tools").and_then(Value::as_array) else {
        return;
    };
    let mut ttl_walk = TtlOrderingWalk::new();
    for (i, tool) in tools.iter().enumerate() {
        if let Some(marker) = tool.get("cache_control") {
            let ttl = extract_ttl(marker);
            tracing::debug!(
                field = "tools",
                tool_index = i,
                ttl = ttl.as_deref().unwrap_or("default"),
                "cache_control marker found"
            );
            ttl_walk.observe(ttl.as_deref());
        }
    }
    ttl_walk.warn_if_violated("tools");
}

fn extract_ttl(marker: &Value) -> Option<String> {
    marker.get("ttl")?.as_str().map(str::to_owned)
}

struct TtlOrderingWalk {
    seen_5m: bool,
    violated: bool,
}

impl TtlOrderingWalk {
    fn new() -> Self {
        Self {
            seen_5m: false,
            violated: false,
        }
    }

    fn observe(&mut self, ttl: Option<&str>) {
        let is_5m = matches!(ttl, None | Some(CACHE_TTL_5M));
        let is_1h = matches!(ttl, Some(CACHE_TTL_1H));

        if is_5m {
            self.seen_5m = true;
        } else if is_1h && self.seen_5m {
            self.violated = true;
        }
    }

    fn warn_if_violated(&self, field: &'static str) {
        if self.violated {
            tracing::warn!(
                field = field,
                rule = "anthropic_prompt_caching_guide_2_19",
                "cache_control TTL ordering violation: 1h marker appears after 5m marker; \
                 cache eviction may be suboptimal but request is forwarded"
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn no_markers_yields_zero() {
        let body = json!({
            "model": "claude-3-5-sonnet-20241022",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        });
        assert_eq!(compute_frozen_count(&body), 0);
    }

    #[test]
    fn marker_at_message_zero_yields_one() {
        let body = json!({
            "messages": [
                {"role": "user", "content": [
                    {"type": "text", "text": "first", "cache_control": {"type": "ephemeral"}},
                ]},
                {"role": "assistant", "content": "second"},
            ],
        });
        assert_eq!(compute_frozen_count(&body), 1);
    }

    #[test]
    fn marker_in_system_does_not_bump() {
        let body = json!({
            "system": [
                {"type": "text", "text": "you are helpful", "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [
                {"role": "user", "content": "hi"},
            ],
        });
        assert_eq!(compute_frozen_count(&body), 0);
    }

    #[test]
    fn marker_in_tools_does_not_bump() {
        let body = json!({
            "tools": [
                {"name": "search", "description": "search", "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [
                {"role": "user", "content": "hi"},
            ],
        });
        assert_eq!(compute_frozen_count(&body), 0);
    }

    #[test]
    fn missing_messages_yields_zero() {
        let body = json!({"model": "claude"});
        assert_eq!(compute_frozen_count(&body), 0);
    }

    #[test]
    fn string_content_yields_zero() {
        let body = json!({
            "messages": [
                {"role": "user", "content": "plain string"},
                {"role": "assistant", "content": "another string"},
            ],
        });
        assert_eq!(compute_frozen_count(&body), 0);
    }

    #[test]
    fn ttl_extracted_when_present() {
        let m = json!({"type": "ephemeral", "ttl": "1h"});
        assert_eq!(extract_ttl(&m).as_deref(), Some("1h"));
    }

    #[test]
    fn ttl_missing_returns_none() {
        let m = json!({"type": "ephemeral"});
        assert_eq!(extract_ttl(&m), None);
    }

    #[test]
    fn ttl_walker_accepts_1h_before_5m() {
        let mut w = TtlOrderingWalk::new();
        w.observe(Some("1h"));
        w.observe(Some("5m"));
        assert!(!w.violated);
    }

    #[test]
    fn ttl_walker_flags_5m_before_1h() {
        let mut w = TtlOrderingWalk::new();
        w.observe(Some("5m"));
        w.observe(Some("1h"));
        assert!(w.violated);
    }

    #[test]
    fn ttl_walker_treats_default_as_5m() {
        let mut w = TtlOrderingWalk::new();
        w.observe(None);
        w.observe(Some("1h"));
        assert!(w.violated);
    }
}
