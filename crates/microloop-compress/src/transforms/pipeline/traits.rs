
use crate::ccr::CcrStore;
use crate::transforms::ContentType;

#[derive(Debug, thiserror::Error)]
pub enum TransformError {
    #[error("invalid input for {transform}: {message}")]
    InvalidInput {
        transform: &'static str,
        message: String,
    },
    #[error("{transform} skipped: {message}")]
    Skipped {
        transform: &'static str,
        message: String,
    },
    #[error("{transform} internal error: {message}")]
    Internal {
        transform: &'static str,
        message: String,
    },
}

impl TransformError {
    pub fn invalid_input(transform: &'static str, message: impl Into<String>) -> Self {
        Self::InvalidInput {
            transform,
            message: message.into(),
        }
    }
    pub fn skipped(transform: &'static str, message: impl Into<String>) -> Self {
        Self::Skipped {
            transform,
            message: message.into(),
        }
    }
    pub fn internal(transform: &'static str, message: impl Into<String>) -> Self {
        Self::Internal {
            transform,
            message: message.into(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct ReformatOutput {
    pub output: String,
    pub bytes_saved: usize,
}

impl ReformatOutput {
    pub fn from_lengths(input_len: usize, output: String) -> Self {
        Self {
            bytes_saved: input_len.saturating_sub(output.len()),
            output,
        }
    }
}

#[derive(Debug, Clone)]
pub struct OffloadOutput {
    pub output: String,
    pub bytes_saved: usize,
    pub cache_key: String,
}

impl OffloadOutput {
    pub fn from_lengths(input_len: usize, output: String, cache_key: String) -> Self {
        Self {
            bytes_saved: input_len.saturating_sub(output.len()),
            output,
            cache_key,
        }
    }
}

#[derive(Debug, Default, Clone)]
pub struct CompressionContext {
    pub query: String,
    pub token_budget: Option<usize>,
}

impl CompressionContext {
    pub fn with_query(query: impl Into<String>) -> Self {
        Self {
            query: query.into(),
            token_budget: None,
        }
    }
    pub fn with_budget(token_budget: usize) -> Self {
        Self {
            query: String::new(),
            token_budget: Some(token_budget),
        }
    }
}

pub trait ReformatTransform: Send + Sync {
    fn name(&self) -> &'static str;

    fn applies_to(&self) -> &[ContentType];

    fn apply(&self, content: &str) -> Result<ReformatOutput, TransformError>;
}

pub trait OffloadTransform: Send + Sync {
    fn name(&self) -> &'static str;

    fn applies_to(&self) -> &[ContentType];

    fn estimate_bloat(&self, content: &str) -> f32;

    fn apply(
        &self,
        content: &str,
        ctx: &CompressionContext,
        store: &dyn CcrStore,
    ) -> Result<OffloadOutput, TransformError>;

    fn confidence(&self) -> f32;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ccr::InMemoryCcrStore;

    pub struct TestReformat;
    impl ReformatTransform for TestReformat {
        fn name(&self) -> &'static str {
            "test_reformat"
        }
        fn applies_to(&self) -> &[ContentType] {
            &[ContentType::PlainText]
        }
        fn apply(&self, content: &str) -> Result<ReformatOutput, TransformError> {
            Ok(ReformatOutput::from_lengths(
                content.len(),
                content.to_string(),
            ))
        }
    }

    pub struct TestOffload {
        bloat: f32,
    }
    impl OffloadTransform for TestOffload {
        fn name(&self) -> &'static str {
            "test_offload"
        }
        fn applies_to(&self) -> &[ContentType] {
            &[ContentType::PlainText]
        }
        fn estimate_bloat(&self, _content: &str) -> f32 {
            self.bloat
        }
        fn apply(
            &self,
            content: &str,
            _ctx: &CompressionContext,
            store: &dyn CcrStore,
        ) -> Result<OffloadOutput, TransformError> {
            let key = format!("test_key_{:024x}", content.len());
            store.put(&key, content);
            Ok(OffloadOutput::from_lengths(
                content.len(),
                content.to_string(),
                key,
            ))
        }
        fn confidence(&self) -> f32 {
            0.5
        }
    }

    #[test]
    fn reformat_output_clamps_negative_savings_to_zero() {
        let r = ReformatOutput::from_lengths(10, "this is much longer than 10 bytes".into());
        assert_eq!(r.bytes_saved, 0);
    }

    #[test]
    fn offload_output_clamps_negative_savings_to_zero() {
        let r = OffloadOutput::from_lengths(10, "this is much longer".into(), "k".into());
        assert_eq!(r.bytes_saved, 0);
    }

    #[test]
    fn transform_error_messages_round_trip() {
        let e = TransformError::invalid_input("json_minifier", "bad token at line 3");
        let msg = e.to_string();
        assert!(msg.contains("json_minifier"));
        assert!(msg.contains("bad token at line 3"));
    }

    #[test]
    fn compression_context_constructors() {
        let q = CompressionContext::with_query("find errors");
        assert_eq!(q.query, "find errors");
        assert_eq!(q.token_budget, None);

        let b = CompressionContext::with_budget(2048);
        assert!(b.query.is_empty());
        assert_eq!(b.token_budget, Some(2048));
    }

    #[test]
    fn reformat_trait_smoke() {
        let t = TestReformat;
        let r = t.apply("hello").expect("reformat passes through");
        assert_eq!(r.output, "hello");
        assert_eq!(r.bytes_saved, 0);
    }

    #[test]
    fn offload_trait_writes_to_store_and_returns_required_cache_key() {
        let store = InMemoryCcrStore::new();
        let t = TestOffload { bloat: 0.9 };
        let r = t
            .apply("hello", &CompressionContext::default(), &store)
            .expect("offload writes");
        assert!(!r.cache_key.is_empty());
        assert_eq!(store.get(&r.cache_key).as_deref(), Some("hello"));
    }

    #[test]
    fn offload_estimate_bloat_is_safe_on_empty_input() {
        let t = TestOffload { bloat: 0.0 };
        let _score = t.estimate_bloat("");
    }
}
