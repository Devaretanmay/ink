
use std::path::Path;
use std::sync::Arc;

use thiserror::Error;
use tokenizers::Tokenizer as HfInner;

use super::{Backend, Tokenizer};

#[derive(Debug, Error)]
pub enum HfTokenizerError {
    #[error("failed to load tokenizer for `{name}`: {source}")]
    Load {
        name: String,
        #[source]
        source: Box<dyn std::error::Error + Send + Sync>,
    },
    #[error("failed to download `{repo}` from HuggingFace Hub: {source}")]
    Hub {
        repo: String,
        #[source]
        source: Box<dyn std::error::Error + Send + Sync>,
    },
}

#[derive(Clone)]
pub struct HfTokenizer {
    name: String,
    inner: Arc<HfInner>,
}

impl std::fmt::Debug for HfTokenizer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("HfTokenizer")
            .field("name", &self.name)
            .finish()
    }
}

impl HfTokenizer {
    pub fn from_bytes(name: impl Into<String>, bytes: &[u8]) -> Result<Self, HfTokenizerError> {
        let name = name.into();
        let inner = HfInner::from_bytes(bytes).map_err(|e| HfTokenizerError::Load {
            name: name.clone(),
            source: e,
        })?;
        Ok(Self {
            name,
            inner: Arc::new(inner),
        })
    }

    pub fn from_file(
        name: impl Into<String>,
        path: impl AsRef<Path>,
    ) -> Result<Self, HfTokenizerError> {
        let name = name.into();
        let inner = HfInner::from_file(path.as_ref()).map_err(|e| HfTokenizerError::Load {
            name: name.clone(),
            source: e,
        })?;
        Ok(Self {
            name,
            inner: Arc::new(inner),
        })
    }

    pub fn from_pretrained(repo: &str) -> Result<Self, HfTokenizerError> {
        let api = hf_hub::api::sync::Api::new().map_err(|e| HfTokenizerError::Hub {
            repo: repo.to_string(),
            source: Box::new(e),
        })?;
        let path = api
            .model(repo.to_string())
            .get("tokenizer.json")
            .map_err(|e| HfTokenizerError::Hub {
                repo: repo.to_string(),
                source: Box::new(e),
            })?;
        Self::from_file(repo, path)
    }

    pub fn name(&self) -> &str {
        &self.name
    }
}

impl Tokenizer for HfTokenizer {
    fn count_text(&self, text: &str) -> usize {
        if text.is_empty() {
            return 0;
        }
        match self.inner.encode(text, false) {
            Ok(enc) => enc.len(),
            Err(_) => 0,
        }
    }

    fn backend(&self) -> Backend {
        Backend::HuggingFace
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const TINY_TOKENIZER_JSON: &str = r#"{
        "version": "1.0",
        "truncation": null,
        "padding": null,
        "added_tokens": [],
        "normalizer": null,
        "pre_tokenizer": {"type": "Whitespace"},
        "post_processor": null,
        "decoder": null,
        "model": {
            "type": "WordLevel",
            "vocab": {"hello": 0, "world": 1, "[UNK]": 2},
            "unk_token": "[UNK]"
        }
    }"#;

    fn tiny() -> HfTokenizer {
        HfTokenizer::from_bytes("tiny-test", TINY_TOKENIZER_JSON.as_bytes())
            .expect("tiny tokenizer.json parses")
    }

    #[test]
    fn empty_is_zero() {
        assert_eq!(tiny().count_text(""), 0);
    }

    #[test]
    fn known_vocab_matches_count() {
        let t = tiny();
        assert_eq!(t.count_text("hello"), 1);
        assert_eq!(t.count_text("hello world"), 2);
        assert_eq!(t.count_text("hello world hello"), 3);
    }

    #[test]
    fn unknown_words_become_unk() {
        let t = tiny();
        assert_eq!(t.count_text("supercalifragilistic"), 1);
        assert_eq!(t.count_text("foo bar baz"), 3);
    }

    #[test]
    fn deterministic() {
        let t = tiny();
        let s = "hello world hello world";
        let first = t.count_text(s);
        for _ in 0..100 {
            assert_eq!(t.count_text(s), first);
        }
    }

    #[test]
    fn unicode_does_not_panic() {
        let t = tiny();
        for s in ["héllo wörld", "你好世界", "🦀 ferris", "\n\t\r"] {
            let n = t.count_text(s);
            assert!(n < s.len() * 4 + 10, "absurd count {n} for {s:?}");
        }
    }

    #[test]
    fn invalid_bytes_returns_error() {
        let r = HfTokenizer::from_bytes("bad", b"not a tokenizer.json");
        assert!(matches!(r, Err(HfTokenizerError::Load { .. })));
    }

    #[test]
    fn name_round_trips() {
        let t = tiny();
        assert_eq!(t.name(), "tiny-test");
    }

    #[test]
    fn backend_is_huggingface() {
        assert_eq!(tiny().backend(), Backend::HuggingFace);
    }

    #[test]
    fn clone_shares_inner() {
        let a = tiny();
        let b = a.clone();
        assert!(Arc::ptr_eq(&a.inner, &b.inner));
    }

    #[test]
    fn from_file_loads_a_real_file() {
        use std::io::Write;
        let dir = std::env::temp_dir().join(format!(
            "headroom-hf-test-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("tokenizer.json");
        let mut f = std::fs::File::create(&path).unwrap();
        f.write_all(TINY_TOKENIZER_JSON.as_bytes()).unwrap();
        drop(f);

        let t = HfTokenizer::from_file("from-file", &path).expect("loads");
        assert_eq!(t.count_text("hello world"), 2);

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    #[ignore = "network-dependent: hits HuggingFace Hub"]
    fn from_pretrained_downloads_real_tokenizer() {
        let t = HfTokenizer::from_pretrained("gpt2").expect("download succeeds");
        assert_eq!(t.count_text("hello world"), 2);
        assert_eq!(t.name(), "gpt2");
        assert_eq!(t.backend(), Backend::HuggingFace);
    }

    #[test]
    fn from_pretrained_invalid_repo_returns_hub_error() {
        let r = HfTokenizer::from_pretrained("");
        assert!(
            matches!(r, Err(HfTokenizerError::Hub { .. })),
            "expected HfTokenizerError::Hub, got {r:?}"
        );
    }
}
