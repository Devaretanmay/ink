use serde::Deserialize;

#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Sensitivity {
    Low,
    Default,
    High,
}

#[derive(Deserialize, Debug, Clone)]
pub struct JsonSchemaSubset {
    pub required: Option<Vec<String>>,
    #[serde(rename = "type")]
    pub schema_type: Option<String>,
}

#[derive(Deserialize, Debug, Clone)]
#[serde(tag = "type")]
#[serde(rename_all = "snake_case")]
pub enum RuleConfig {
    Regex { pattern: String },
    Exact { value: String },
    JsonSchema(JsonSchemaSubset),
}

#[derive(Deserialize, Debug, Clone, PartialEq, Default)]
#[serde(rename_all = "snake_case")]
pub enum CountMode {
    #[default]
    All,
    ErrorsOnly,
}

#[derive(Deserialize, Debug, Clone)]
pub struct TrajectoryGateDefaults {
    #[serde(default = "default_max_repeats")]
    pub max_repeats: usize,
    #[serde(default)]
    pub count_mode: CountMode,
}

#[derive(Deserialize, Debug, Clone)]
pub struct MicroloopDefaults {
    pub trajectory_gate: TrajectoryGateDefaults,
}

#[derive(Deserialize, Debug, Clone)]
pub struct ErrorDetectionCfg {
    pub json_path: Option<String>,
    pub status_field: Option<String>,
    pub status_not_in: Option<Vec<u16>>,
    pub regex: Option<String>,
}

#[derive(Deserialize, Debug, Clone, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum CostTier {
    Low,
    Medium,
    High,
}

#[derive(Deserialize, Debug, Clone)]
pub struct ToolTrajectoryGate {
    pub max_repeats: Option<usize>,
    pub cost_tier: Option<CostTier>,
    pub cost_weight: Option<f32>,
    pub count_mode: Option<CountMode>,
    #[serde(default)]
    pub volatile_fields: Vec<String>,
    pub error_detection: Option<ErrorDetectionCfg>,
}

#[derive(Deserialize, Debug, Clone)]
pub struct ToolConfig {
    pub name: String,
    pub trajectory_gate: ToolTrajectoryGate,
}

#[derive(Deserialize, Debug, Clone)]
pub struct MicroloopConfig {
    #[serde(default = "default_sensitivity")]
    pub sensitivity: Sensitivity,
    #[serde(default = "default_max_repeats")]
    pub max_repeats: usize,
    #[serde(default)]
    pub ignore_args: bool,
    pub history_window: Option<usize>,

    #[serde(default)]
    pub rules: Vec<RuleConfig>,

    pub defaults: Option<MicroloopDefaults>,
    #[serde(default)]
    pub tools: Vec<ToolConfig>,
}

impl MicroloopConfig {
    pub fn from_yaml(yaml_str: &str) -> Result<Self, String> {
        serde_yaml::from_str(yaml_str).map_err(|e| format!("YAML parsing error: {}", e))
    }
}

fn default_sensitivity() -> Sensitivity {
    Sensitivity::Default
}

fn default_max_repeats() -> usize {
    3
}
