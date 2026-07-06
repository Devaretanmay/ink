
use md5::{Digest, Md5};

use crate::ccr::CcrStore;
use crate::transforms::pipeline::config::JsonOffloadConfig;
use crate::transforms::pipeline::traits::{
    CompressionContext, OffloadOutput, OffloadTransform, TransformError,
};
use crate::transforms::smart_crusher::{SmartCrusher, SmartCrusherConfig};
use crate::transforms::ContentType;

const NAME: &str = "json_offload";
const CONFIDENCE: f32 = 0.85;

pub struct JsonOffload {
    crusher: SmartCrusher,
    config: JsonOffloadConfig,
}

impl JsonOffload {
    pub fn new(config: JsonOffloadConfig) -> Self {
        Self {
            crusher: SmartCrusher::new(SmartCrusherConfig::default()),
            config,
        }
    }

    pub fn with_crusher(crusher: SmartCrusher, config: JsonOffloadConfig) -> Self {
        Self { crusher, config }
    }
}

impl OffloadTransform for JsonOffload {
    fn name(&self) -> &'static str {
        NAME
    }

    fn applies_to(&self) -> &[ContentType] {
        &[ContentType::JsonArray]
    }

    fn estimate_bloat(&self, content: &str) -> f32 {
        if content.is_empty() {
            return 0.0;
        }
        let trimmed = content.trim_start();
        if !trimmed.starts_with('[') {
            return 0.0;
        }
        let separators = count_row_separators(content);
        if separators < self.config.min_array_rows.saturating_sub(1) {
            return 0.0;
        }
        let saturation = self.config.saturation_rows.saturating_sub(1).max(1);
        (separators as f32 / saturation as f32).clamp(0.0, 1.0)
    }

    fn apply(
        &self,
        content: &str,
        ctx: &CompressionContext,
        store: &dyn CcrStore,
    ) -> Result<OffloadOutput, TransformError> {
        let result = self.crusher.crush(content, &ctx.query, 0.0);
        if !result.was_modified {
            return Err(TransformError::skipped(
                NAME,
                "smart crusher returned passthrough",
            ));
        }
        if result.compressed.len() >= content.len() {
            return Err(TransformError::skipped(NAME, "no savings after crush"));
        }

        let key = md5_hex_24(content);
        store.put(&key, content);
        let mut output = result.compressed;
        output.push_str("\n[json_offload CCR: hash=");
        output.push_str(&key);
        output.push(']');

        Ok(OffloadOutput::from_lengths(content.len(), output, key))
    }

    fn confidence(&self) -> f32 {
        CONFIDENCE
    }
}

fn count_row_separators(content: &str) -> usize {
    content.matches("},{").count()
        + content.matches("}, {").count()
        + content.matches("},\n").count()
}

fn md5_hex_24(content: &str) -> String {
    let mut h = Md5::new();
    h.update(content.as_bytes());
    let digest = h.finalize();
    let mut hex = String::with_capacity(32);
    for b in digest.iter() {
        hex.push_str(&format!("{:02x}", b));
    }
    hex.truncate(24);
    hex
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ccr::InMemoryCcrStore;
    use crate::transforms::pipeline::config::PipelineConfig;

    fn cfg() -> JsonOffloadConfig {
        PipelineConfig::default().offload.json
    }

    fn offload() -> JsonOffload {
        JsonOffload::new(cfg())
    }

    fn build_tabular_array(n: usize) -> String {
        let mut s = String::from("[");
        for i in 0..n {
            if i > 0 {
                s.push(',');
            }
            s.push_str(&format!(
                "{{\"id\":{},\"name\":\"item-{}\",\"value\":{}}}",
                i,
                i,
                i * 100
            ));
        }
        s.push(']');
        s
    }

    #[test]
    fn name_and_applies_to() {
        let o = offload();
        assert_eq!(o.name(), "json_offload");
        assert_eq!(o.applies_to(), &[ContentType::JsonArray]);
    }

    #[test]
    fn estimate_bloat_empty_input_zero() {
        assert_eq!(offload().estimate_bloat(""), 0.0);
    }

    #[test]
    fn estimate_bloat_non_array_input_zero() {
        assert_eq!(offload().estimate_bloat(r#"{"a":1,"b":2}"#), 0.0);
        assert_eq!(offload().estimate_bloat("just words here"), 0.0);
        assert_eq!(offload().estimate_bloat("42"), 0.0);
    }

    #[test]
    fn estimate_bloat_below_min_rows_zero() {
        let arr = build_tabular_array(3);
        assert_eq!(offload().estimate_bloat(&arr), 0.0);
    }

    #[test]
    fn estimate_bloat_at_saturation_is_one() {
        let arr = build_tabular_array(100);
        let score = offload().estimate_bloat(&arr);
        assert!(score >= 0.99, "expected ~1.0 at saturation, got {score}");
    }

    #[test]
    fn estimate_bloat_scales_linearly_in_middle_range() {
        let arr = build_tabular_array(25);
        let score = offload().estimate_bloat(&arr);
        assert!(
            (0.4..=0.6).contains(&score),
            "expected mid-range score, got {score}"
        );
    }

    #[test]
    fn estimate_bloat_handles_whitespace_after_array_open() {
        let mut s = String::from("[\n");
        for i in 0..30 {
            if i > 0 {
                s.push_str(",\n");
            }
            s.push_str(&format!(
                "  {{\"id\":{i},\"name\":\"item-{i}\",\"value\":{}}}",
                i * 100
            ));
        }
        s.push_str("\n]");
        let score = offload().estimate_bloat(&s);
        assert!(
            score > 0.4,
            "pretty-printed array should still score, got {score}"
        );
    }

    #[test]
    fn estimate_bloat_handles_huge_input_safely() {
        let arr = build_tabular_array(50_000);
        let score = offload().estimate_bloat(&arr);
        assert!((0.99..=1.0).contains(&score));
    }

    #[test]
    fn apply_compresses_large_tabular_array_and_stores_original() {
        let arr = build_tabular_array(500);
        let store = InMemoryCcrStore::new();
        let r = offload()
            .apply(&arr, &CompressionContext::default(), &store)
            .expect("smart crusher should compress");
        assert!(r.bytes_saved > 0);
        assert!(!r.cache_key.is_empty());
        assert!(r.output.contains("[json_offload CCR: hash="));
        assert_eq!(store.get(&r.cache_key).as_deref(), Some(arr.as_str()));
    }

    #[test]
    fn apply_skipped_when_smart_crusher_passes_through() {
        let arr = build_tabular_array(2);
        let store = InMemoryCcrStore::new();
        let err = offload()
            .apply(&arr, &CompressionContext::default(), &store)
            .expect_err("must skip");
        match err {
            TransformError::Skipped { transform, .. } => assert_eq!(transform, "json_offload"),
            _ => panic!("expected Skipped, got {err:?}"),
        }
        assert_eq!(store.len(), 0);
    }

    #[test]
    fn apply_skipped_for_non_json_input() {
        let store = InMemoryCcrStore::new();
        let err = offload()
            .apply("not json at all", &CompressionContext::default(), &store)
            .expect_err("must skip non-json");
        match err {
            TransformError::Skipped { .. } => {}
            _ => panic!("expected Skipped, got {err:?}"),
        }
    }

    #[test]
    fn apply_propagates_query_anchors_into_smart_crusher() {
        let mut s = String::from("[");
        for i in 0..50 {
            if i > 0 {
                s.push(',');
            }
            let name = if i == 17 {
                "needle".to_string()
            } else {
                format!("hay-{i}")
            };
            s.push_str(&format!(
                "{{\"id\":{i},\"name\":\"{name}\",\"score\":{}}}",
                i % 7
            ));
        }
        s.push(']');
        let store = InMemoryCcrStore::new();
        let ctx = CompressionContext::with_query("needle");
        let r = offload()
            .apply(&s, &ctx, &store)
            .expect("crusher should run");
        assert!(
            r.output.contains("needle"),
            "anchor row should survive crush"
        );
    }

    #[test]
    fn cache_key_is_stable_across_calls_for_same_input() {
        let arr = build_tabular_array(100);
        let store_a = InMemoryCcrStore::new();
        let store_b = InMemoryCcrStore::new();
        let r_a = offload()
            .apply(&arr, &CompressionContext::default(), &store_a)
            .expect("ok");
        let r_b = offload()
            .apply(&arr, &CompressionContext::default(), &store_b)
            .expect("ok");
        assert_eq!(
            r_a.cache_key, r_b.cache_key,
            "cache_key should be a deterministic hash of input"
        );
    }

    #[test]
    fn count_row_separators_handles_compact_and_pretty() {
        assert_eq!(count_row_separators(""), 0);
        assert_eq!(count_row_separators("[]"), 0);
        assert_eq!(count_row_separators(r#"[{"a":1}]"#), 0);
        assert_eq!(count_row_separators(r#"[{"a":1},{"a":2}]"#), 1);
        assert_eq!(count_row_separators(r#"[{"a":1}, {"a":2}, {"a":3}]"#), 2);
    }
}
