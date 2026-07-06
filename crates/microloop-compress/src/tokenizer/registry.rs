
use std::collections::HashMap;
use std::sync::{OnceLock, RwLock};

use super::{EstimatingCounter, HfTokenizer, HfTokenizerError, TiktokenCounter, Tokenizer};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Backend {
    Tiktoken,
    HuggingFace,
    Estimation,
}

pub fn detect_backend(model: &str) -> Backend {
    let m = model.to_ascii_lowercase();

    if m.starts_with("gpt-4o")
        || m.starts_with("gpt-4")
        || m.starts_with("gpt-3.5")
        || m.starts_with("o1")
        || m.starts_with("o3")
        || m.starts_with("text-embedding")
        || m.starts_with("text-davinci")
        || m.starts_with("davinci")
        || m.starts_with("curie")
        || m.starts_with("babbage")
        || m.starts_with("ada")
        || m.starts_with("code-")
    {
        return Backend::Tiktoken;
    }

    Backend::Estimation
}

pub fn get_tokenizer(model: &str) -> Box<dyn Tokenizer> {
    if let Some(hf) = lookup_hf(model) {
        return Box::new(hf);
    }
    match detect_backend(model) {
        Backend::Tiktoken => match TiktokenCounter::for_model(model) {
            Ok(t) => Box::new(t),
            Err(_) => Box::new(default_estimator_for(model)),
        },
        Backend::HuggingFace | Backend::Estimation => Box::new(default_estimator_for(model)),
    }
}

fn default_estimator_for(model: &str) -> EstimatingCounter {
    let m = model.to_ascii_lowercase();
    if m.starts_with("claude-") {
        EstimatingCounter::new(3.5)
    } else if m.starts_with("gemini") || m.starts_with("palm") || m.starts_with("command") {
        EstimatingCounter::new(4.0)
    } else {
        EstimatingCounter::default()
    }
}


fn hf_table() -> &'static RwLock<HashMap<String, HfTokenizer>> {
    static TABLE: OnceLock<RwLock<HashMap<String, HfTokenizer>>> = OnceLock::new();
    TABLE.get_or_init(|| RwLock::new(HashMap::new()))
}

pub fn register_hf(prefix: impl Into<String>, tokenizer: HfTokenizer) {
    let key = prefix.into().to_ascii_lowercase();
    hf_table()
        .write()
        .expect("hf registry poisoned")
        .insert(key, tokenizer);
}

pub fn clear_hf_registrations() {
    hf_table().write().expect("hf registry poisoned").clear();
}

pub fn try_register_hf(prefix: &str, repo: &str) -> Result<(), HfTokenizerError> {
    let t = HfTokenizer::from_pretrained(repo)?;
    register_hf(prefix, t);
    Ok(())
}

fn lookup_hf(model: &str) -> Option<HfTokenizer> {
    let m = model.to_ascii_lowercase();
    let table = hf_table().read().expect("hf registry poisoned");
    table
        .iter()
        .filter(|(prefix, _)| m.starts_with(prefix.as_str()))
        .max_by_key(|(prefix, _)| prefix.len())
        .map(|(_, t)| t.clone())
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

    fn tiny(name: &str) -> HfTokenizer {
        HfTokenizer::from_bytes(name, TINY_TOKENIZER_JSON.as_bytes()).unwrap()
    }

    static REGISTRY_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    struct RegistryGuard<'a> {
        _g: std::sync::MutexGuard<'a, ()>,
    }
    impl<'a> RegistryGuard<'a> {
        fn acquire() -> Self {
            let g = REGISTRY_LOCK.lock().unwrap_or_else(|e| e.into_inner());
            clear_hf_registrations();
            Self { _g: g }
        }
    }
    impl<'a> Drop for RegistryGuard<'a> {
        fn drop(&mut self) {
            clear_hf_registrations();
        }
    }

    #[test]
    fn openai_models_pick_tiktoken() {
        for m in [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4",
            "gpt-4-turbo",
            "gpt-3.5-turbo",
            "o1-preview",
            "o3-mini",
            "text-embedding-3-small",
            "text-davinci-003",
            "davinci",
            "babbage-002",
            "code-davinci-002",
        ] {
            assert_eq!(detect_backend(m), Backend::Tiktoken, "{m}");
        }
    }

    #[test]
    fn non_openai_models_fall_through_to_estimation() {
        for m in [
            "claude-haiku-4-5-20251001",
            "claude-3-opus",
            "gemini-1.5-pro",
            "command-r-plus",
            "llama-3-70b",
            "mistral-large",
            "qwen-72b",
            "made-up-model-name",
        ] {
            assert_eq!(detect_backend(m), Backend::Estimation, "{m}");
        }
    }

    #[test]
    fn case_insensitive() {
        assert_eq!(detect_backend("GPT-4o"), Backend::Tiktoken);
        assert_eq!(detect_backend("Claude-haiku"), Backend::Estimation);
    }

    #[test]
    fn estimator_density_per_family() {
        let _g = RegistryGuard::acquire();
        let claude = get_tokenizer("claude-3-opus");
        assert_eq!(claude.count_text(&"a".repeat(35)), 10);

        let gemini = get_tokenizer("gemini-1.5-pro");
        assert_eq!(gemini.count_text(&"a".repeat(40)), 10);
    }

    #[test]
    fn registered_hf_wins_over_estimator() {
        let _g = RegistryGuard::acquire();
        register_hf("command-", tiny("cohere"));
        let t = get_tokenizer("command-r-plus");
        assert_eq!(t.count_text("hello world"), 2);
        assert_eq!(t.backend(), Backend::HuggingFace);
    }

    #[test]
    fn registered_hf_does_not_override_tiktoken() {
        let _g = RegistryGuard::acquire();
        register_hf("gpt-4o", tiny("oops"));
        let t = get_tokenizer("gpt-4o-mini");
        assert_eq!(t.backend(), Backend::HuggingFace);
    }

    #[test]
    fn longest_prefix_wins() {
        let _g = RegistryGuard::acquire();
        register_hf("command-", tiny("general"));
        register_hf("command-r-plus", tiny("specific"));
        let t = get_tokenizer("command-r-plus");
        assert_eq!(t.backend(), Backend::HuggingFace);
        let count_specific = t.count_text("hello world hello");
        assert_eq!(count_specific, 3);

        let t2 = get_tokenizer("command-light");
        assert_eq!(t2.backend(), Backend::HuggingFace);
    }

    #[test]
    fn case_insensitive_registration() {
        let _g = RegistryGuard::acquire();
        register_hf("Command-", tiny("cohere"));
        let t = get_tokenizer("COMMAND-R-PLUS");
        assert_eq!(t.backend(), Backend::HuggingFace);
    }

    #[test]
    fn clear_resets_state() {
        let _g = RegistryGuard::acquire();
        register_hf("command-", tiny("cohere"));
        clear_hf_registrations();
        let t = get_tokenizer("command-r-plus");
        assert_eq!(t.backend(), Backend::Estimation);
    }

    #[test]
    fn unrelated_models_still_estimate() {
        let _g = RegistryGuard::acquire();
        register_hf("command-", tiny("cohere"));
        let t = get_tokenizer("claude-3-opus");
        assert_eq!(t.backend(), Backend::Estimation);
    }

    #[test]
    fn detect_backend_ignores_runtime_registrations() {
        let _g = RegistryGuard::acquire();
        register_hf("command-", tiny("cohere"));
        assert_eq!(detect_backend("command-r-plus"), Backend::Estimation);
    }
}
