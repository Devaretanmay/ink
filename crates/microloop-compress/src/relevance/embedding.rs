
use std::sync::Mutex;

use fastembed::{EmbeddingModel, InitOptions, TextEmbedding};

use super::base::{RelevanceScore, RelevanceScorer};

pub struct EmbeddingScorer {
    pub model_name: String,
    model: Option<Mutex<TextEmbedding>>,
}

impl Default for EmbeddingScorer {
    fn default() -> Self {
        EmbeddingScorer {
            model_name: "BAAI/bge-small-en-v1.5".to_string(),
            model: None,
        }
    }
}

impl EmbeddingScorer {
    pub fn try_new() -> Result<Self, String> {
        Self::try_new_with_model(EmbeddingModel::BGESmallENV15)
    }

    pub fn try_new_with_model(model_kind: EmbeddingModel) -> Result<Self, String> {
        if !crate::onnx_cpu::onnx_runtime_supported_by_cpu() {
            return Err("EmbeddingScorer: ONNX Runtime backend requires AVX2 on \
                 this x86 CPU; embedding relevance disabled (falling back to BM25)"
                .to_string());
        }
        let name = format!("{:?}", model_kind);
        let model = TextEmbedding::try_new(InitOptions::new(model_kind))
            .map_err(|e| format!("EmbeddingScorer model load failed: {}", e))?;
        Ok(EmbeddingScorer {
            model_name: name,
            model: Some(Mutex::new(model)),
        })
    }
}

impl RelevanceScorer for EmbeddingScorer {
    fn score(&self, item: &str, context: &str) -> RelevanceScore {
        if item.is_empty() || context.is_empty() {
            return RelevanceScore::empty("Embedding: empty input");
        }
        let Some(model) = &self.model else {
            return RelevanceScore::empty("Embedding: model not available");
        };
        let mut guard = match model.lock() {
            Ok(g) => g,
            Err(_) => return RelevanceScore::empty("Embedding: lock poisoned"),
        };
        let embeddings = match guard.embed(vec![item.to_string(), context.to_string()], None) {
            Ok(e) => e,
            Err(e) => return RelevanceScore::empty(format!("Embedding: inference failed: {}", e)),
        };
        if embeddings.len() != 2 {
            return RelevanceScore::empty("Embedding: unexpected embedding count");
        }
        let sim = cosine_similarity(&embeddings[0], &embeddings[1]);
        RelevanceScore::new(
            sim,
            format!("Embedding: semantic similarity {:.2}", sim),
            Vec::new(),
        )
    }

    fn score_batch(&self, items: &[&str], context: &str) -> Vec<RelevanceScore> {
        if items.is_empty() {
            return Vec::new();
        }
        if context.is_empty() {
            return items
                .iter()
                .map(|_| RelevanceScore::empty("Embedding: empty context"))
                .collect();
        }
        let Some(model) = &self.model else {
            return items
                .iter()
                .map(|_| RelevanceScore::empty("Embedding: model not available"))
                .collect();
        };
        let mut guard = match model.lock() {
            Ok(g) => g,
            Err(_) => {
                return items
                    .iter()
                    .map(|_| RelevanceScore::empty("Embedding: lock poisoned"))
                    .collect();
            }
        };

        let mut all_texts: Vec<String> = items.iter().map(|s| s.to_string()).collect();
        all_texts.push(context.to_string());
        let embeddings = match guard.embed(all_texts, None) {
            Ok(e) => e,
            Err(e) => {
                return items
                    .iter()
                    .map(|_| RelevanceScore::empty(format!("Embedding: inference failed: {}", e)))
                    .collect();
            }
        };
        if embeddings.len() != items.len() + 1 {
            return items
                .iter()
                .map(|_| RelevanceScore::empty("Embedding: unexpected embedding count"))
                .collect();
        }

        let context_emb = embeddings.last().unwrap().clone();
        embeddings
            .iter()
            .take(items.len())
            .map(|emb| {
                let sim = cosine_similarity(emb, &context_emb);
                RelevanceScore::new(sim, format!("Embedding: {:.2}", sim), Vec::new())
            })
            .collect()
    }

    fn is_available(&self) -> bool {
        self.model.is_some()
    }
}

fn cosine_similarity(a: &[f32], b: &[f32]) -> f64 {
    if a.is_empty() || b.is_empty() || a.len() != b.len() {
        return 0.0;
    }
    let mut dot: f64 = 0.0;
    let mut norm_a: f64 = 0.0;
    let mut norm_b: f64 = 0.0;
    for i in 0..a.len() {
        let av = a[i] as f64;
        let bv = b[i] as f64;
        dot += av * bv;
        norm_a += av * av;
        norm_b += bv * bv;
    }
    if norm_a == 0.0 || norm_b == 0.0 {
        return 0.0;
    }
    let sim = dot / (norm_a.sqrt() * norm_b.sqrt());
    sim.clamp(0.0, 1.0)
}

#[cfg(test)]
mod tests {
    use super::*;


    fn fastembed_enabled() -> bool {
        std::env::var("RUN_FASTEMBED_TESTS").is_ok()
    }

    fn unavailable_scorer() -> EmbeddingScorer {
        EmbeddingScorer {
            model_name: "test".to_string(),
            model: None,
        }
    }

    #[test]
    fn cosine_similarity_orthogonal_vectors() {
        let a = vec![1.0_f32, 0.0, 0.0, 0.0];
        let b = vec![0.0_f32, 1.0, 0.0, 0.0];
        assert_eq!(cosine_similarity(&a, &b), 0.0);
    }

    #[test]
    fn cosine_similarity_identical_vectors() {
        let v = vec![1.0_f32, 2.0, 3.0];
        let sim = cosine_similarity(&v, &v);
        assert!((sim - 1.0).abs() < 1e-9, "got {}", sim);
    }

    #[test]
    fn cosine_similarity_opposite_clamped_to_zero() {
        let a = vec![1.0_f32, 1.0];
        let b = vec![-1.0_f32, -1.0];
        assert_eq!(cosine_similarity(&a, &b), 0.0);
    }

    #[test]
    fn cosine_similarity_zero_vector_returns_zero() {
        let zero = vec![0.0_f32; 4];
        let v = vec![1.0_f32, 2.0, 3.0, 4.0];
        assert_eq!(cosine_similarity(&zero, &v), 0.0);
        assert_eq!(cosine_similarity(&v, &zero), 0.0);
    }

    #[test]
    fn cosine_similarity_mismatched_dim_returns_zero() {
        let a = vec![1.0_f32, 2.0];
        let b = vec![1.0_f32, 2.0, 3.0];
        assert_eq!(cosine_similarity(&a, &b), 0.0);
    }


    #[test]
    fn unavailable_scorer_returns_empty_scores() {
        let s = unavailable_scorer();
        assert!(!s.is_available());

        let r = s.score("item", "query");
        assert_eq!(r.score, 0.0);

        let batch = s.score_batch(&["a", "b", "c"], "query");
        assert_eq!(batch.len(), 3);
        for sc in batch {
            assert_eq!(sc.score, 0.0);
        }
    }

    #[test]
    fn unavailable_scorer_empty_inputs_short_circuit() {
        let s = unavailable_scorer();
        let r = s.score("", "query");
        assert_eq!(r.score, 0.0);
        assert!(r.reason.contains("empty"));
    }

    #[test]
    fn batch_with_empty_items_returns_empty_vec() {
        let s = unavailable_scorer();
        let r = s.score_batch(&[], "anything");
        assert!(r.is_empty());
    }


    #[test]
    fn onnx_guard_matches_cpu_features() {
        let supported = crate::onnx_cpu::onnx_runtime_supported_by_cpu();
        #[cfg(any(target_arch = "x86", target_arch = "x86_64"))]
        assert_eq!(supported, std::is_x86_feature_detected!("avx2"));
        #[cfg(not(any(target_arch = "x86", target_arch = "x86_64")))]
        assert!(supported);
    }

    #[test]
    fn try_new_errors_on_unsupported_cpu_instead_of_sigill() {
        if crate::onnx_cpu::onnx_runtime_supported_by_cpu() {
            return;
        }
        match EmbeddingScorer::try_new() {
            Err(err) => assert!(err.contains("AVX2"), "unexpected error: {err}"),
            Ok(_) => panic!("ONNX backend must not load on a no-AVX2 CPU"),
        }
    }


    #[test]
    fn fastembed_loads_default_model() {
        if !fastembed_enabled() {
            return;
        }
        let s = EmbeddingScorer::try_new().expect("model loads");
        assert!(s.is_available());
        assert_eq!(s.model_name, "BGESmallENV15");
    }

    #[test]
    fn fastembed_semantic_match_outranks_unrelated() {
        if !fastembed_enabled() {
            return;
        }
        let s = EmbeddingScorer::try_new().expect("model loads");
        let related = s.score("authentication failed for user", "login error");
        let unrelated = s.score("the weather is nice today", "login error");
        assert!(
            related.score > unrelated.score,
            "semantically-related text should score higher: related={}, unrelated={}",
            related.score,
            unrelated.score
        );
    }

    #[test]
    fn fastembed_batch_returns_one_score_per_item() {
        if !fastembed_enabled() {
            return;
        }
        let s = EmbeddingScorer::try_new().expect("model loads");
        let items = ["foo", "bar", "baz"];
        let scores = s.score_batch(&items, "query text");
        assert_eq!(scores.len(), 3);
        for sc in scores {
            assert!((0.0..=1.0).contains(&sc.score));
        }
    }
}
