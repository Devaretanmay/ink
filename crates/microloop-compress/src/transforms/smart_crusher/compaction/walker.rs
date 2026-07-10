
use std::sync::Arc;

use serde_json::{Map, Value};

use super::classifier::{classify_cell, CellClass};
use super::compactor::{compact, CompactConfig};
use super::formatter::{CsvSchemaFormatter, Formatter};
use super::ir::OpaqueKind;
use crate::ccr::CcrStore;

use blake3;

pub struct DocumentCompactor {
    pub config: CompactConfig,
    pub formatter: Box<dyn Formatter>,
    pub ccr_store: Option<Arc<dyn CcrStore>>,
}

impl Default for DocumentCompactor {
    fn default() -> Self {
        Self {
            config: CompactConfig::default(),
            formatter: Box::new(CsvSchemaFormatter::new()),
            ccr_store: None,
        }
    }
}

impl DocumentCompactor {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_formatter(mut self, formatter: Box<dyn Formatter>) -> Self {
        self.formatter = formatter;
        self
    }

    pub fn with_config(mut self, config: CompactConfig) -> Self {
        self.config = config;
        self
    }

    pub fn with_ccr_store(mut self, store: Arc<dyn CcrStore>) -> Self {
        self.ccr_store = Some(store);
        self
    }

    pub fn compact(&self, doc: Value) -> Value {
        walk(doc, self)
    }
}

fn walk(v: Value, ctx: &DocumentCompactor) -> Value {
    match v {
        Value::Object(map) => walk_object(map, ctx),
        Value::Array(items) => walk_array(items, ctx),
        Value::String(s) => walk_string(s, ctx),
        scalar => scalar,
    }
}

fn walk_object(map: Map<String, Value>, ctx: &DocumentCompactor) -> Value {
    Value::Object(map.into_iter().map(|(k, v)| (k, walk(v, ctx))).collect())
}

fn walk_array(items: Vec<Value>, ctx: &DocumentCompactor) -> Value {
    let inner: Vec<Value> = items.into_iter().map(|i| walk(i, ctx)).collect();

    let c = compact(&inner, &ctx.config);
    if c.was_compacted() {
        Value::String(ctx.formatter.format(&c))
    } else {
        Value::Array(inner)
    }
}

fn walk_string(s: String, ctx: &DocumentCompactor) -> Value {
    if let Some(parsed) = try_parse_json_container(&s) {
        let recursed = walk(parsed, ctx);
        return match recursed {
            Value::String(rendered) => Value::String(rendered),
            other => Value::String(serde_json::to_string(&other).unwrap_or(s)),
        };
    }

    if let CellClass::Opaque(kind) = classify_cell(&Value::String(s.clone()), &ctx.config.classify)
    {
        return Value::String(emit_opaque_ccr_marker(&s, &kind, ctx.ccr_store.as_ref()));
    }

    Value::String(s)
}

pub fn try_parse_json_container(s: &str) -> Option<Value> {
    let trimmed = s.trim_start();
    if !matches!(trimmed.chars().next(), Some('{') | Some('[')) {
        return None;
    }
    serde_json::from_str::<Value>(s)
        .ok()
        .filter(|v| matches!(v, Value::Object(_) | Value::Array(_)))
}

pub fn emit_opaque_ccr_marker(
    payload: &str,
    kind: &OpaqueKind,
    store: Option<&Arc<dyn CcrStore>>,
) -> String {
    let h = blake3::hash(payload.as_bytes());
    let hash = h.to_hex().as_str()[..12].to_string();
    if let Some(s) = store {
        s.put(&hash, payload);
    }
    let kind_str = match kind {
        OpaqueKind::Base64Blob => "base64",
        OpaqueKind::LongString => "string",
        OpaqueKind::HtmlChunk => "html",
        OpaqueKind::Other(s) => s.as_str(),
    };
    format!("<<ccr:{},{},{}>>", hash, kind_str, humanize(payload.len()))
}

fn humanize(n: usize) -> String {
    if n < 1024 {
        return format!("{n}B");
    }
    let kb = n as f64 / 1024.0;
    if kb < 1024.0 {
        return format!("{kb:.1}KB");
    }
    format!("{:.1}MB", kb / 1024.0)
}

pub fn compact_document(doc: Value) -> Value {
    DocumentCompactor::new().compact(doc)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn dc() -> DocumentCompactor {
        DocumentCompactor::new()
    }

    #[test]
    fn top_level_array_of_objects_is_compacted() {
        let doc = json!([
            {"id": 1, "name": "alice"},
            {"id": 2, "name": "bob"},
            {"id": 3, "name": "carol"},
        ]);
        let out = dc().compact(doc);
        match out {
            Value::String(s) => {
                assert!(s.starts_with("[3]{"), "got: {s}");
                assert!(s.contains("name:string"));
            }
            other => panic!("expected String, got {other:?}"),
        }
    }

    #[test]
    fn nested_array_in_object_field_is_compacted_in_place() {
        let doc = json!({
            "user": "alice",
            "events": [
                {"id": 1, "action": "click"},
                {"id": 2, "action": "hover"},
                {"id": 3, "action": "submit"},
            ],
        });
        let out = dc().compact(doc);
        let obj = out.as_object().expect("object preserved");
        assert_eq!(obj.get("user").and_then(|v| v.as_str()), Some("alice"));
        let events = obj.get("events").and_then(|v| v.as_str()).expect("string");
        assert!(events.starts_with("[3]{"), "got: {events}");
    }

    #[test]
    fn deeply_nested_arrays_compact_at_every_level() {
        let doc = json!({
            "outer": {
                "middle": {
                    "rows": [
                        {"a": 1, "b": "x"},
                        {"a": 2, "b": "y"},
                    ],
                },
            },
        });
        let out = dc().compact(doc);
        let inner = out
            .pointer("/outer/middle/rows")
            .and_then(|v| v.as_str())
            .expect("rows compacted to string");
        assert!(inner.starts_with("[2]{"), "got: {inner}");
    }

    #[test]
    fn stringified_json_in_field_is_parsed_and_compacted() {
        let inner = r#"[{"x":1},{"x":2},{"x":3}]"#;
        let doc = json!({
            "id": "abc",
            "payload": inner,
        });
        let out = dc().compact(doc);
        let payload = out
            .pointer("/payload")
            .and_then(|v| v.as_str())
            .expect("payload compacted");
        assert!(payload.starts_with("[3]{"), "got: {payload}");
    }

    #[test]
    fn long_opaque_string_at_top_level_becomes_ccr_marker() {
        let big = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=".repeat(8);
        let out = dc().compact(Value::String(big));
        match out {
            Value::String(s) => assert!(
                s.starts_with("<<ccr:") && s.contains(",base64,"),
                "got: {s}"
            ),
            other => panic!("expected String, got {other:?}"),
        }
    }

    #[test]
    fn long_opaque_string_inside_object_field_becomes_ccr_marker() {
        let big = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=".repeat(8);
        let doc = json!({"id": 1, "blob": big});
        let out = dc().compact(doc);
        let blob = out.pointer("/blob").and_then(|v| v.as_str()).unwrap();
        assert!(blob.starts_with("<<ccr:"), "got: {blob}");
    }

    #[test]
    fn pure_scalar_object_unchanged() {
        let doc = json!({"a": 1, "b": "short", "c": true, "d": null});
        let out = dc().compact(doc.clone());
        assert_eq!(out, doc);
    }

    #[test]
    fn mixed_doc_only_compactable_parts_change() {
        let doc = json!({
            "user_id": 42,
            "tag": "active",
            "events": [
                {"id": 1, "kind": "x"},
                {"id": 2, "kind": "y"},
            ],
            "config": {"region": "us", "tier": "gold"},
        });
        let out = dc().compact(doc);
        assert_eq!(out.pointer("/user_id"), Some(&json!(42)));
        assert_eq!(out.pointer("/tag"), Some(&json!("active")));
        assert!(out
            .pointer("/config")
            .map(|v| v.is_object())
            .unwrap_or(false));
        assert!(out
            .pointer("/events")
            .and_then(|v| v.as_str())
            .unwrap()
            .starts_with("[2]{"));
    }

    #[test]
    fn cascading_recursion_outer_table_sees_inner_compacted_string() {
        let doc = json!([
            {"id": 1, "payload": r#"[{"x":1},{"x":2},{"x":3}]"#},
            {"id": 2, "payload": r#"[{"x":4},{"x":5}]"#},
        ]);
        let out = dc().compact(doc);
        match out {
            Value::String(s) => {
                assert!(s.starts_with("[2]{"), "outer table: {s}");
                assert!(s.contains("[3]{") || s.contains("\"[3]{"));
            }
            other => panic!("expected String, got {other:?}"),
        }
    }

    #[test]
    fn array_of_scalars_left_alone() {
        let doc = json!([1, 2, 3, "four", 5.0]);
        let out = dc().compact(doc.clone());
        assert_eq!(out, doc);
    }

    #[test]
    fn empty_object_unchanged() {
        let doc = json!({});
        assert_eq!(dc().compact(doc.clone()), doc);
    }

    #[test]
    fn empty_array_unchanged() {
        let doc = json!([]);
        assert_eq!(dc().compact(doc.clone()), doc);
    }

    #[test]
    fn malformed_stringified_json_left_alone() {
        let doc = json!({"payload": "{not valid json"});
        let out = dc().compact(doc.clone());
        assert_eq!(out, doc);
    }
}
