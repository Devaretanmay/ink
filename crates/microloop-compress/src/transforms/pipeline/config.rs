
use serde::Deserialize;

const DEFAULT_TOML: &str = include_str!("../../../config/pipeline.toml");

#[derive(Debug, Clone, Deserialize)]
pub struct PipelineConfig {
    pub pipeline: OrchestratorConfig,
    pub bloat: BloatConfigs,
    pub reformat: ReformatConfigs,
    pub offload: OffloadConfigs,
}

impl PipelineConfig {
    pub fn from_default_str() -> Self {
        toml::from_str(DEFAULT_TOML)
            .expect("embedded config/pipeline.toml must parse — checked by tests")
    }

    pub fn from_toml_str(s: &str) -> Result<Self, ConfigError> {
        toml::from_str(s).map_err(ConfigError::from)
    }

    pub fn from_file(path: impl AsRef<std::path::Path>) -> Result<Self, ConfigError> {
        let text = std::fs::read_to_string(path).map_err(ConfigError::Io)?;
        Self::from_toml_str(&text)
    }
}

impl Default for PipelineConfig {
    fn default() -> Self {
        Self::from_default_str()
    }
}

#[derive(Debug, Clone, Copy, Deserialize)]
pub struct OrchestratorConfig {
    pub reformat_target_ratio: f64,
    pub bloat_threshold: f32,
    pub offload_fallback_ratio: f64,
}

#[derive(Debug, Clone, Copy, Deserialize)]
pub struct BloatConfigs {
    pub log: LogBloatConfig,
    pub diff: DiffBloatConfig,
    pub search: SearchBloatConfig,
}

#[derive(Debug, Clone, Copy, Deserialize)]
pub struct LogBloatConfig {
    pub min_lines: usize,
    pub sample_size: usize,
    pub high_priority_threshold: f32,
    pub uniqueness_weight: f32,
    pub priority_dilution_weight: f32,
}

#[derive(Debug, Clone, Copy, Deserialize)]
pub struct DiffBloatConfig {
    pub min_lines: usize,
    pub normal_context_ratio: f64,
}

#[derive(Debug, Clone, Copy, Deserialize)]
pub struct SearchBloatConfig {
    pub min_matches: usize,
    pub cluster_threshold: f32,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ReformatConfigs {
    pub log_template: LogTemplateConfig,
}

#[derive(Debug, Clone, Copy, Deserialize)]
pub struct LogTemplateConfig {
    pub min_lines: usize,
    pub min_run: usize,
    pub similarity_threshold: f32,
    pub min_constant_tokens: usize,
}

#[derive(Debug, Clone, Deserialize)]
pub struct OffloadConfigs {
    pub json: JsonOffloadConfig,
    pub diff_noise: DiffNoiseConfig,
}

#[derive(Debug, Clone, Copy, Deserialize)]
pub struct JsonOffloadConfig {
    pub min_array_rows: usize,
    pub saturation_rows: usize,
}

#[derive(Debug, Clone, Deserialize)]
pub struct DiffNoiseConfig {
    pub min_lines: usize,
    pub lockfile_suffixes: Vec<String>,
    pub drop_whitespace_only_hunks: bool,
}

#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("invalid pipeline config TOML: {0}")]
    Parse(#[from] toml::de::Error),
    #[error("could not read pipeline config file: {0}")]
    Io(std::io::Error),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn from_default_str_does_not_panic() {
        let _ = PipelineConfig::from_default_str();
    }

    #[test]
    fn defaults_match_documented_thresholds() {
        let cfg = PipelineConfig::default();
        assert_eq!(cfg.pipeline.reformat_target_ratio, 0.5);
        assert_eq!(cfg.pipeline.bloat_threshold, 0.5);
        assert_eq!(cfg.pipeline.offload_fallback_ratio, 0.85);
        assert_eq!(cfg.bloat.log.min_lines, 50);
        assert_eq!(cfg.bloat.log.sample_size, 100);
        assert_eq!(cfg.bloat.diff.min_lines, 50);
        assert_eq!(cfg.bloat.search.min_matches, 10);
    }

    #[test]
    fn bloat_log_weights_sum_to_at_most_one() {
        let cfg = PipelineConfig::default();
        let total = cfg.bloat.log.uniqueness_weight + cfg.bloat.log.priority_dilution_weight;
        assert!(
            total <= 1.0001,
            "log bloat weights must sum to ≤ 1.0, got {total}"
        );
    }

    #[test]
    fn from_toml_str_overrides_defaults() {
        let toml = r#"
            [pipeline]
            reformat_target_ratio = 0.3
            bloat_threshold = 0.7
            offload_fallback_ratio = 0.9

            [bloat.log]
            min_lines = 25
            sample_size = 50
            high_priority_threshold = 0.6
            uniqueness_weight = 0.4
            priority_dilution_weight = 0.6

            [bloat.diff]
            min_lines = 30
            normal_context_ratio = 0.7

            [bloat.search]
            min_matches = 5
            cluster_threshold = 20.0

            [reformat.log_template]
            min_lines = 10
            min_run = 5
            similarity_threshold = 0.8
            min_constant_tokens = 3

            [offload.json]
            min_array_rows = 3
            saturation_rows = 25

            [offload.diff_noise]
            min_lines = 20
            lockfile_suffixes = ["custom.lock"]
            drop_whitespace_only_hunks = false
        "#;
        let cfg = PipelineConfig::from_toml_str(toml).expect("override parses");
        assert_eq!(cfg.pipeline.reformat_target_ratio, 0.3);
        assert_eq!(cfg.bloat.log.min_lines, 25);
        assert_eq!(cfg.bloat.search.cluster_threshold, 20.0);
        assert_eq!(cfg.reformat.log_template.min_run, 5);
        assert_eq!(
            cfg.offload.diff_noise.lockfile_suffixes,
            vec!["custom.lock"]
        );
    }

    #[test]
    fn defaults_carry_reformat_and_offload_sections() {
        let cfg = PipelineConfig::default();
        assert_eq!(cfg.reformat.log_template.min_lines, 20);
        assert_eq!(cfg.reformat.log_template.min_run, 3);
        assert_eq!(cfg.offload.json.min_array_rows, 5);
        assert_eq!(cfg.offload.json.saturation_rows, 50);
        assert!(!cfg.offload.diff_noise.lockfile_suffixes.is_empty());
        assert!(cfg
            .offload
            .diff_noise
            .lockfile_suffixes
            .iter()
            .any(|s| s == "Cargo.lock"));
    }

    #[test]
    fn malformed_toml_returns_error() {
        let r = PipelineConfig::from_toml_str("this is not toml = [unterminated");
        assert!(r.is_err(), "malformed TOML should fail loudly");
    }

    #[test]
    fn missing_section_returns_error() {
        let toml = r#"
            [pipeline]
            reformat_target_ratio = 0.5
            bloat_threshold = 0.5
            offload_fallback_ratio = 0.85
        "#;
        assert!(PipelineConfig::from_toml_str(toml).is_err());
    }
}
