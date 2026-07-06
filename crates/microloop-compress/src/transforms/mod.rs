
pub mod adaptive_sizer;
pub mod anchor_selector;
pub mod content_detector;
pub mod detection;
pub mod diff_compressor;
pub mod live_zone;
pub mod log_compressor;
pub mod magika_detector;
pub mod pipeline;
pub mod recommendations;
pub mod safety;
pub mod search_compressor;
pub mod smart_crusher;
pub mod tag_protector;
pub mod text_crusher;
pub mod unidiff_detector;

pub use content_detector::{
    detect_content_type, is_json_array_of_dicts, ContentType, DetectionResult,
};
pub use detection::detect;
pub use diff_compressor::{
    DiffCompressionResult, DiffCompressor, DiffCompressorConfig, DiffCompressorStats,
};
pub use live_zone::{
    compress_anthropic_live_zone, compress_openai_chat_live_zone,
    compress_openai_responses_live_zone, summarize_openai_responses_no_change_reason, AuthMode,
    BlockAction, BlockOutcome, CompressionManifest, ExclusionReason, LiveZoneError,
    LiveZoneOutcome,
};
pub use log_compressor::{
    LogCompressionResult, LogCompressor, LogCompressorConfig, LogCompressorStats, LogFormat,
    LogLevel, LogLine,
};
pub use magika_detector::{magika_detect, map_magika_label, MagikaDetectorError};
pub use pipeline::{
    CompressionContext, CompressionPipeline, CompressionPipelineBuilder, DiffNoise, DiffOffload,
    JsonMinifier, JsonOffload, LogOffload, LogTemplate, OffloadOutput, OffloadTransform,
    PipelineConfig, PipelineResult, ReformatOutput, ReformatTransform, TransformError,
};
pub use recommendations::{Recommendation, RecommendationStore, RECOMMENDATIONS_PATH_ENV_VAR};
pub use safety::{tool_pair_indices, ToolPair};
pub use search_compressor::{
    FileMatches, SearchCompressionResult, SearchCompressor, SearchCompressorConfig,
    SearchCompressorStats, SearchMatch,
};
pub use tag_protector::{is_known_html_tag, protect_tags, restore_tags, ProtectStats};
pub use text_crusher::{TextCrusher, TextCrusherConfig, TextCrusherResult};
pub use unidiff_detector::{detect_diff, is_diff};
