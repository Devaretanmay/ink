
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use serde::Deserialize;

pub const RECOMMENDATIONS_PATH_ENV_VAR: &str = "HEADROOM_RECOMMENDATIONS_PATH";

const DEFAULT_RECOMMENDATIONS_PATH: &str = "./recommendations.toml";

pub use super::live_zone::AuthMode;

#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct Recommendation {
    pub auth_mode: String,
    pub model_family: String,
    pub structure_hash: String,
    pub strategy_hint: String,
    pub confidence: f64,
    pub observations: u64,
}

#[derive(Debug, Default, Deserialize)]
struct RecommendationFile {
    #[serde(default)]
    recommendation: Vec<Recommendation>,
}

#[derive(Debug, Default, Clone)]
pub struct RecommendationStore {
    by_key: HashMap<(String, String, String), Recommendation>,
}

impl RecommendationStore {
    pub fn empty() -> Self {
        Self {
            by_key: HashMap::new(),
        }
    }

    pub fn len(&self) -> usize {
        self.by_key.len()
    }

    pub fn is_empty(&self) -> bool {
        self.by_key.is_empty()
    }

    pub fn lookup(
        &self,
        auth_mode: AuthMode,
        model_family: &str,
        structure_hash: &str,
    ) -> Option<&Recommendation> {
        let key = (
            auth_mode.as_str().to_string(),
            model_family.to_string(),
            structure_hash.to_string(),
        );
        self.by_key.get(&key)
    }

    pub fn from_toml_str(s: &str) -> Result<Self, RecommendationsError> {
        let parsed: RecommendationFile = toml::from_str(s).map_err(RecommendationsError::Parse)?;
        let mut by_key = HashMap::with_capacity(parsed.recommendation.len());
        for row in parsed.recommendation {
            let key = (
                row.auth_mode.clone(),
                row.model_family.clone(),
                row.structure_hash.clone(),
            );
            by_key.insert(key, row);
        }
        Ok(Self { by_key })
    }

    pub fn from_file(path: impl AsRef<Path>) -> Result<Self, RecommendationsError> {
        let path = path.as_ref();
        let text = std::fs::read_to_string(path).map_err(|e| {
            if e.kind() == std::io::ErrorKind::NotFound {
                RecommendationsError::Missing(path.to_path_buf())
            } else {
                RecommendationsError::Io {
                    path: path.to_path_buf(),
                    source: e,
                }
            }
        })?;
        Self::from_toml_str(&text)
    }

    pub fn load_or_empty(path: impl AsRef<Path>) -> Self {
        let path = path.as_ref();
        match Self::from_file(path) {
            Ok(store) => {
                tracing::info!(
                    event = "recommendations_loaded",
                    path = %path.display(),
                    rows = store.len(),
                    "TOIN recommendations loaded",
                );
                store
            }
            Err(RecommendationsError::Missing(_)) => {
                tracing::info!(
                    event = "recommendations_missing",
                    path = %path.display(),
                    "no recommendations.toml present; using static defaults",
                );
                Self::empty()
            }
            Err(err) => {
                tracing::warn!(
                    event = "recommendations_load_failed",
                    path = %path.display(),
                    error = %err,
                    "TOIN recommendations failed to load — falling back to empty store",
                );
                Self::empty()
            }
        }
    }
}

static GLOBAL: OnceLock<RecommendationStore> = OnceLock::new();

pub fn default_path() -> PathBuf {
    std::env::var(RECOMMENDATIONS_PATH_ENV_VAR)
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(DEFAULT_RECOMMENDATIONS_PATH))
}

pub fn load_default() -> &'static RecommendationStore {
    GLOBAL.get_or_init(|| RecommendationStore::load_or_empty(default_path()))
}

pub fn get(
    auth_mode: AuthMode,
    model: &str,
    structure_hash: &str,
) -> Option<&'static Recommendation> {
    load_default().lookup(auth_mode, model, structure_hash)
}

#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum RecommendationsError {
    #[error("recommendations file not found: {0}")]
    Missing(PathBuf),
    #[error("recommendations IO error at {path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("recommendations TOML parse error: {0}")]
    Parse(#[from] toml::de::Error),
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_toml() -> &'static str {
        r#"
[[recommendation]]
auth_mode = "payg"
model_family = "claude-3-5"
structure_hash = "deadbeef"
strategy_hint = "smart_crusher"
confidence = 0.87
observations = 142

[[recommendation]]
auth_mode = "oauth"
model_family = "gpt-4o"
structure_hash = "cafebabe"
strategy_hint = "log_compressor"
confidence = 0.42
observations = 60
"#
    }

    #[test]
    fn from_toml_str_indexes_by_tuple_key() {
        let store = RecommendationStore::from_toml_str(sample_toml()).expect("parses");
        assert_eq!(store.len(), 2);

        let r = store
            .lookup(AuthMode::Payg, "claude-3-5", "deadbeef")
            .expect("hit");
        assert_eq!(r.strategy_hint, "smart_crusher");
        assert!((r.confidence - 0.87).abs() < 1e-9);
        assert_eq!(r.observations, 142);
    }

    #[test]
    fn lookup_returns_none_for_missing_slice() {
        let store = RecommendationStore::from_toml_str(sample_toml()).expect("parses");
        assert!(store
            .lookup(AuthMode::Unknown, "gpt-4o", "cafebabe")
            .is_none());
    }

    #[test]
    fn empty_store_lookup_is_none() {
        let store = RecommendationStore::empty();
        assert!(store.is_empty());
        assert!(store.lookup(AuthMode::Payg, "claude-3-5", "any").is_none());
    }

    #[test]
    fn malformed_toml_yields_parse_error() {
        let bad = "this is not valid toml [[\n\n";
        let err = RecommendationStore::from_toml_str(bad).unwrap_err();
        assert!(matches!(err, RecommendationsError::Parse(_)));
    }

    #[test]
    fn auth_mode_strings_match_python_publish_cli() {
        assert_eq!(AuthMode::Payg.as_str(), "payg");
        assert_eq!(AuthMode::OAuth.as_str(), "oauth");
        assert_eq!(AuthMode::Subscription.as_str(), "subscription");
        assert_eq!(AuthMode::Unknown.as_str(), "unknown");
    }
}
