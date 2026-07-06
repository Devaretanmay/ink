
use std::collections::HashSet;

use crate::ccr::CcrStore;
use crate::signals::{ImportanceContext, KeywordDetector, LineImportanceDetector};
use crate::transforms::log_compressor::{LogCompressor, LogCompressorConfig};
use crate::transforms::pipeline::config::LogBloatConfig;
use crate::transforms::pipeline::traits::{
    CompressionContext, OffloadOutput, OffloadTransform, TransformError,
};
use crate::transforms::ContentType;

const NAME: &str = "log_offload";
const CONFIDENCE: f32 = 0.85;

pub struct LogOffload {
    compressor: LogCompressor,
    bloat: LogBloatConfig,
    detector: Box<dyn LineImportanceDetector>,
    bias: f64,
}

impl LogOffload {
    pub fn new(bloat: LogBloatConfig) -> Self {
        Self::with_compressor(
            LogCompressor::new(LogCompressorConfig::default()),
            bloat,
            Box::new(KeywordDetector::new()),
        )
    }

    pub fn with_compressor(
        compressor: LogCompressor,
        bloat: LogBloatConfig,
        detector: Box<dyn LineImportanceDetector>,
    ) -> Self {
        Self {
            compressor,
            bloat,
            detector,
            bias: 0.0,
        }
    }

    pub fn with_bias(mut self, bias: f64) -> Self {
        self.bias = bias;
        self
    }
}

impl OffloadTransform for LogOffload {
    fn name(&self) -> &'static str {
        NAME
    }

    fn applies_to(&self) -> &[ContentType] {
        &[ContentType::BuildOutput]
    }

    fn estimate_bloat(&self, content: &str) -> f32 {
        if content.is_empty() {
            return 0.0;
        }
        let mut unique: HashSet<&str> = HashSet::with_capacity(self.bloat.sample_size);
        let mut sampled = 0usize;
        let mut low_priority = 0usize;

        for line in content.lines() {
            if sampled >= self.bloat.sample_size {
                break;
            }
            sampled += 1;
            unique.insert(line);
            let signal = self.detector.score(line, ImportanceContext::Log);
            if signal.priority <= self.bloat.high_priority_threshold {
                low_priority += 1;
            }
        }

        let total_lines = content.lines().count();
        if total_lines < self.bloat.min_lines {
            return 0.0;
        }
        if sampled == 0 {
            return 0.0;
        }

        let repetition = 1.0 - (unique.len() as f32 / sampled as f32);
        let dilution = low_priority as f32 / sampled as f32;
        let score = repetition * self.bloat.uniqueness_weight
            + dilution * self.bloat.priority_dilution_weight;
        score.clamp(0.0, 1.0)
    }

    fn apply(
        &self,
        content: &str,
        _ctx: &CompressionContext,
        store: &dyn CcrStore,
    ) -> Result<OffloadOutput, TransformError> {
        let (result, stats) = self
            .compressor
            .compress_with_store(content, self.bias, Some(store));

        let Some(key) = result.cache_key else {
            let reason = stats.ccr_skip_reason.unwrap_or("no cache_key emitted");
            return Err(TransformError::skipped(NAME, reason));
        };

        Ok(OffloadOutput::from_lengths(
            content.len(),
            result.compressed,
            key,
        ))
    }

    fn confidence(&self) -> f32 {
        CONFIDENCE
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ccr::InMemoryCcrStore;
    use crate::transforms::pipeline::config::PipelineConfig;

    fn default_bloat() -> LogBloatConfig {
        PipelineConfig::default().bloat.log
    }

    fn offload() -> LogOffload {
        LogOffload::new(default_bloat())
    }

    #[test]
    fn name_and_applies_to() {
        let o = offload();
        assert_eq!(o.name(), "log_offload");
        assert_eq!(o.applies_to(), &[ContentType::BuildOutput]);
    }

    #[test]
    fn estimate_bloat_empty_input_is_zero() {
        assert_eq!(offload().estimate_bloat(""), 0.0);
    }

    #[test]
    fn estimate_bloat_below_min_lines_is_zero() {
        let log = "INFO: starting\nERROR: oh no\nINFO: heartbeat\nINFO: done\nINFO: bye";
        assert_eq!(offload().estimate_bloat(log), 0.0);
    }

    #[test]
    fn estimate_bloat_high_repetition_scores_high() {
        let line = "INFO: heartbeat received from worker-7";
        let log: Vec<&str> = (0..100).map(|_| line).collect();
        let log = log.join("\n");
        let score = offload().estimate_bloat(&log);
        assert!(score > 0.8, "expected high score, got {score}");
    }

    #[test]
    fn estimate_bloat_unique_errors_score_low() {
        let lines: Vec<String> = (0..100)
            .map(|i| format!("ERROR: failure number {i} at module x"))
            .collect();
        let log = lines.join("\n");
        let score = offload().estimate_bloat(&log);
        assert!(score < 0.3, "expected low score, got {score}");
    }

    #[test]
    fn estimate_bloat_priority_dilution_alone_scores_meaningfully() {
        let lines: Vec<String> = (0..100)
            .map(|i| format!("INFO: routine event #{i}"))
            .collect();
        let log = lines.join("\n");
        let score = offload().estimate_bloat(&log);
        assert!((0.3..=0.7).contains(&score), "expected ~0.5, got {score}");
    }

    #[test]
    fn estimate_bloat_safe_on_huge_inputs() {
        let lines: Vec<String> = (0..100_000).map(|i| format!("line {i}")).collect();
        let log = lines.join("\n");
        let _ = offload().estimate_bloat(&log);
    }

    #[test]
    fn apply_emits_cache_key_and_stores_original_for_repetitive_log() {
        let line = "INFO: heartbeat\n";
        let log: String = line.repeat(200);
        let store = InMemoryCcrStore::new();
        let r = offload()
            .apply(&log, &CompressionContext::default(), &store)
            .expect("offload should produce a key on bloaty input");
        assert!(!r.cache_key.is_empty());
        assert_eq!(store.get(&r.cache_key).as_deref(), Some(log.as_str()));
        assert!(r.bytes_saved > 0);
    }

    #[test]
    fn apply_returns_skipped_when_underlying_compressor_declines_ccr() {
        let log = "INFO: a\nINFO: b\nINFO: c\nINFO: d\nINFO: e";
        let store = InMemoryCcrStore::new();
        let err = offload()
            .apply(log, &CompressionContext::default(), &store)
            .expect_err("must skip");
        match err {
            TransformError::Skipped { transform, .. } => assert_eq!(transform, "log_offload"),
            _ => panic!("expected Skipped, got {err:?}"),
        }
        assert_eq!(store.len(), 0, "no payload should have been stored");
    }
}
