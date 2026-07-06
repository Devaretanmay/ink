
use std::sync::{Arc, LazyLock};

use thiserror::Error;
use tiktoken_rs::CoreBPE;

use super::{Backend, Tokenizer};

#[derive(Debug, Error)]
pub enum TiktokenError {
    #[error("unknown encoding for model `{0}`")]
    UnknownEncoding(String),
}

static O200K: LazyLock<Arc<CoreBPE>> =
    LazyLock::new(|| Arc::new(tiktoken_rs::o200k_base().expect("o200k_base init")));
static CL100K: LazyLock<Arc<CoreBPE>> =
    LazyLock::new(|| Arc::new(tiktoken_rs::cl100k_base().expect("cl100k_base init")));
static P50K: LazyLock<Arc<CoreBPE>> =
    LazyLock::new(|| Arc::new(tiktoken_rs::p50k_base().expect("p50k_base init")));
static R50K: LazyLock<Arc<CoreBPE>> =
    LazyLock::new(|| Arc::new(tiktoken_rs::r50k_base().expect("r50k_base init")));

pub struct TiktokenCounter {
    model: String,
    encoding_name: &'static str,
    bpe: Arc<CoreBPE>,
}

impl std::fmt::Debug for TiktokenCounter {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TiktokenCounter")
            .field("model", &self.model)
            .field("encoding", &self.encoding_name)
            .finish()
    }
}

impl TiktokenCounter {
    pub fn for_model(model: &str) -> Result<Self, TiktokenError> {
        let encoding_name = encoding_for(model)?;
        let bpe = match encoding_name {
            "o200k_base" => O200K.clone(),
            "cl100k_base" => CL100K.clone(),
            "p50k_base" => P50K.clone(),
            "r50k_base" => R50K.clone(),
            _ => return Err(TiktokenError::UnknownEncoding(model.to_string())),
        };
        Ok(Self {
            model: model.to_string(),
            encoding_name,
            bpe,
        })
    }

    pub fn model(&self) -> &str {
        &self.model
    }

    pub fn encoding_name(&self) -> &'static str {
        self.encoding_name
    }
}

impl Tokenizer for TiktokenCounter {
    fn count_text(&self, text: &str) -> usize {
        if text.is_empty() {
            return 0;
        }
        self.bpe.encode_ordinary(text).len()
    }

    fn backend(&self) -> Backend {
        Backend::Tiktoken
    }
}

fn encoding_for(model: &str) -> Result<&'static str, TiktokenError> {
    let m = model.to_ascii_lowercase();

    if m.starts_with("gpt-4o") || m.starts_with("o1") || m.starts_with("o3") {
        return Ok("o200k_base");
    }

    if m.starts_with("gpt-4") || m.starts_with("gpt-3.5") || m.starts_with("text-embedding") {
        return Ok("cl100k_base");
    }

    if m.starts_with("code-")
        || m.starts_with("text-davinci-002")
        || m.starts_with("text-davinci-003")
    {
        return Ok("p50k_base");
    }

    if m.starts_with("text-davinci")
        || m.starts_with("davinci")
        || m.starts_with("curie")
        || m.starts_with("babbage")
        || m.starts_with("ada")
    {
        return Ok("r50k_base");
    }

    Err(TiktokenError::UnknownEncoding(model.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_string_is_zero() {
        let t = TiktokenCounter::for_model("gpt-4o-mini").unwrap();
        assert_eq!(t.count_text(""), 0);
    }

    #[test]
    fn nonempty_text_is_at_least_one_token() {
        let t = TiktokenCounter::for_model("gpt-4o-mini").unwrap();
        assert!(t.count_text("a") >= 1);
    }

    #[test]
    fn known_token_counts_for_o200k() {
        let t = TiktokenCounter::for_model("gpt-4o-mini").unwrap();
        assert_eq!(t.count_text("hello"), 1);
        assert_eq!(t.count_text("Hello, world!"), 4);
        assert_eq!(
            t.count_text("the quick brown fox jumps over the lazy dog"),
            9
        );
    }

    #[test]
    fn determinism() {
        let t = TiktokenCounter::for_model("gpt-4o").unwrap();
        let s = "Determinism check across many calls.";
        let first = t.count_text(s);
        for _ in 0..1000 {
            assert_eq!(t.count_text(s), first);
        }
    }

    #[test]
    fn unicode_input_does_not_panic() {
        let t = TiktokenCounter::for_model("gpt-4o-mini").unwrap();
        for s in [
            "héllo wörld",
            "你好世界",
            "مرحبا بالعالم",
            "🦀 ferris the crab",
            "\n\t\r\x07",
        ] {
            let n = t.count_text(s);
            assert!(n >= 1, "{s:?}");
            assert!(n < s.len() * 4 + 10, "absurd count {n} for {s:?}");
        }
    }

    #[test]
    fn very_long_input() {
        let t = TiktokenCounter::for_model("gpt-4o-mini").unwrap();
        let s = "the quick brown fox ".repeat(50_000);
        let n = t.count_text(&s);
        assert!(n > 100_000 && n < 1_000_000, "n={n}");
    }

    #[test]
    fn encoding_dispatch() {
        for (model, expected) in [
            ("gpt-4o", "o200k_base"),
            ("gpt-4o-mini", "o200k_base"),
            ("gpt-4o-2024-08-06", "o200k_base"),
            ("o1-preview", "o200k_base"),
            ("o3-mini", "o200k_base"),
            ("gpt-4", "cl100k_base"),
            ("gpt-4-turbo", "cl100k_base"),
            ("gpt-3.5-turbo", "cl100k_base"),
            ("text-embedding-3-small", "cl100k_base"),
            ("code-davinci-002", "p50k_base"),
            ("text-davinci-002", "p50k_base"),
            ("text-davinci-003", "p50k_base"),
            ("text-davinci-001", "r50k_base"),
            ("davinci", "r50k_base"),
            ("curie", "r50k_base"),
            ("babbage", "r50k_base"),
            ("ada", "r50k_base"),
        ] {
            let t = TiktokenCounter::for_model(model)
                .unwrap_or_else(|e| panic!("for_model({model}) failed: {e}"));
            assert_eq!(t.encoding_name(), expected, "{model}");
        }
    }

    #[test]
    fn unknown_model_returns_error() {
        let r = TiktokenCounter::for_model("claude-3-opus");
        assert!(matches!(r, Err(TiktokenError::UnknownEncoding(_))));
    }

    #[test]
    fn case_insensitive_dispatch() {
        let t = TiktokenCounter::for_model("GPT-4o-Mini").unwrap();
        assert_eq!(t.encoding_name(), "o200k_base");
    }

    #[test]
    fn shared_bpe_instances() {
        let a = TiktokenCounter::for_model("gpt-4o").unwrap();
        let b = TiktokenCounter::for_model("gpt-4o-mini").unwrap();
        assert!(Arc::ptr_eq(&a.bpe, &b.bpe));
    }

    #[test]
    fn backend_is_tiktoken() {
        let t = TiktokenCounter::for_model("gpt-4o-mini").unwrap();
        assert_eq!(t.backend(), Backend::Tiktoken);
    }
}
