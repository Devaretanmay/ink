
mod base;
mod bm25;
mod embedding;
mod hybrid;

pub use base::{default_batch_score, RelevanceScore, RelevanceScorer};
pub use bm25::BM25Scorer;
pub use embedding::EmbeddingScorer;
pub use hybrid::HybridScorer;

pub fn create_scorer(tier: &str) -> Result<Box<dyn RelevanceScorer + Send + Sync>, String> {
    match tier.to_lowercase().as_str() {
        "bm25" => Ok(Box::new(BM25Scorer::default())),
        "hybrid" => Ok(Box::new(HybridScorer::default())),
        "embedding" => {
            let s = EmbeddingScorer::default();
            if s.is_available() {
                Ok(Box::new(s))
            } else {
                Err(
                    "EmbeddingScorer requires the ONNX backend (not yet implemented in Rust)"
                        .to_string(),
                )
            }
        }
        other => Err(format!(
            "Unknown scorer tier: {}. Valid tiers: bm25, embedding, hybrid",
            other
        )),
    }
}
