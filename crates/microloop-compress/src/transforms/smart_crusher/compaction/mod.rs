
pub mod classifier;
pub mod compactor;
pub mod formatter;
pub mod ir;
pub mod walker;

pub use classifier::{classify_cell, CellClass, ClassifyConfig};
pub use compactor::{compact, compact_with_store, CompactConfig};
pub use formatter::{CsvSchemaFormatter, Formatter, JsonFormatter, MarkdownKvFormatter};
pub use ir::{Bucket, CellValue, Compaction, FieldSpec, OpaqueKind, Row, Schema};
pub use walker::{
    compact_document, emit_opaque_ccr_marker, try_parse_json_container, DocumentCompactor,
};

pub struct CompactionStage {
    pub config: CompactConfig,
    pub formatter: Box<dyn Formatter>,
}

impl CompactionStage {
    pub fn default_csv_schema() -> Self {
        Self {
            config: CompactConfig::default(),
            formatter: Box::new(CsvSchemaFormatter::new()),
        }
    }

    pub fn csv_schema(config: CompactConfig) -> Self {
        Self {
            config,
            formatter: Box::new(CsvSchemaFormatter::new()),
        }
    }

    pub fn default_json() -> Self {
        Self {
            config: CompactConfig::default(),
            formatter: Box::new(JsonFormatter::new()),
        }
    }

    pub fn default_markdown_kv() -> Self {
        Self {
            config: CompactConfig::default(),
            formatter: Box::new(MarkdownKvFormatter::new()),
        }
    }

    pub const SUPPORTED_FORMAT_NAMES: &'static [&'static str] =
        &["csv-schema", "json", "markdown-kv"];

    pub fn from_format_name(name: &str) -> Option<Self> {
        match name {
            "csv-schema" => Some(Self::default_csv_schema()),
            "json" => Some(Self::default_json()),
            "markdown-kv" => Some(Self::default_markdown_kv()),
            _ => None,
        }
    }

    pub fn run(&self, items: &[serde_json::Value]) -> (Compaction, String) {
        let c = compact(items, &self.config);
        let rendered = self.formatter.format(&c);
        (c, rendered)
    }

    pub fn run_with_store(
        &self,
        items: &[serde_json::Value],
        store: Option<&std::sync::Arc<dyn crate::ccr::CcrStore>>,
    ) -> (Compaction, String) {
        let c = compact_with_store(items, &self.config, store);
        let rendered = self.formatter.format(&c);
        (c, rendered)
    }
}

impl std::fmt::Debug for CompactionStage {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("CompactionStage")
            .field("config", &self.config)
            .field("formatter", &self.formatter.name())
            .finish()
    }
}
