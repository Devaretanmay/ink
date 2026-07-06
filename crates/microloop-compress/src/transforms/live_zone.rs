
use std::{collections::HashSet, sync::OnceLock};

use serde::Deserialize;
use serde_json::value::RawValue;
use serde_json::Value;
use thiserror::Error;

use super::content_detector::{detect_content_type, ContentType};
use super::diff_compressor::{DiffCompressor, DiffCompressorConfig};
use super::log_compressor::{LogCompressor, LogCompressorConfig};
use super::search_compressor::{SearchCompressor, SearchCompressorConfig};
use super::smart_crusher::{SmartCrusher, SmartCrusherConfig};
use super::text_crusher::{TextCrusher, TextCrusherConfig};
use crate::ccr::{compute_key, marker_for, CcrStore};
use crate::tokenizer::get_tokenizer;


const STRATEGY_SMART_CRUSHER: &str = "smart_crusher";
const STRATEGY_LOG_COMPRESSOR: &str = "log_compressor";
const STRATEGY_SEARCH_COMPRESSOR: &str = "search_compressor";
const STRATEGY_DIFF_COMPRESSOR: &str = "diff_compressor";
const STRATEGY_TEXT_CRUSHER: &str = "text_crusher";

const EMPTY_QUERY: &str = "";
const DEFAULT_BIAS: f64 = 0.0;

pub const DEFAULT_MODEL: &str = "claude-3-5-sonnet-20241022";


const THRESHOLD_JSON_ARRAY: usize = 512;
const THRESHOLD_BUILD_OUTPUT: usize = 512;
const THRESHOLD_SEARCH_RESULTS: usize = 512;
const THRESHOLD_GIT_DIFF: usize = 512;
const THRESHOLD_SOURCE_CODE: usize = 512;
const THRESHOLD_PLAIN_TEXT: usize = 512;
const THRESHOLD_HTML: usize = 512;

fn threshold_for(content_type: ContentType) -> usize {
    match content_type {
        ContentType::JsonArray => THRESHOLD_JSON_ARRAY,
        ContentType::BuildOutput => THRESHOLD_BUILD_OUTPUT,
        ContentType::SearchResults => THRESHOLD_SEARCH_RESULTS,
        ContentType::GitDiff => THRESHOLD_GIT_DIFF,
        ContentType::SourceCode => THRESHOLD_SOURCE_CODE,
        ContentType::PlainText => THRESHOLD_PLAIN_TEXT,
        ContentType::Html => THRESHOLD_HTML,
    }
}


#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum AuthMode {
    Payg,
    OAuth,
    Subscription,
    Unknown,
}

impl AuthMode {
    pub fn as_str(self) -> &'static str {
        match self {
            AuthMode::Payg => "payg",
            AuthMode::OAuth => "oauth",
            AuthMode::Subscription => "subscription",
            AuthMode::Unknown => "unknown",
        }
    }
}

impl From<crate::auth_mode::AuthMode> for AuthMode {
    fn from(mode: crate::auth_mode::AuthMode) -> Self {
        match mode {
            crate::auth_mode::AuthMode::Payg => AuthMode::Payg,
            crate::auth_mode::AuthMode::OAuth => AuthMode::OAuth,
            crate::auth_mode::AuthMode::Subscription => AuthMode::Subscription,
        }
    }
}

#[derive(Debug, Clone)]
pub struct BlockOutcome {
    pub message_index: usize,
    pub block_index: Option<usize>,
    pub block_type: String,
    pub action: BlockAction,
}

#[derive(Debug, Clone)]
pub enum BlockAction {
    NoCompressionApplied {
        content_type: String,
    },
    Compressed {
        strategy: &'static str,
        original_bytes: usize,
        compressed_bytes: usize,
        original_tokens: usize,
        compressed_tokens: usize,
    },
    CompressorError {
        strategy: &'static str,
        error: String,
    },
    RejectedNotSmaller {
        strategy: &'static str,
        original_bytes: usize,
        compressed_bytes: usize,
        original_tokens: usize,
        compressed_tokens: usize,
    },
    BelowByteThreshold {
        content_type: &'static str,
        byte_count: usize,
        threshold_bytes: usize,
    },
    Excluded { reason: ExclusionReason },
}

#[derive(Debug, Clone, Copy)]
pub enum ExclusionReason {
    BelowFrozenFloor,
    AboveLiveZone,
    HotZoneBlockType,
}

#[derive(Debug, Clone)]
pub struct CompressionManifest {
    pub messages_total: usize,
    pub messages_below_frozen_floor: usize,
    pub latest_user_message_index: Option<usize>,
    pub block_outcomes: Vec<BlockOutcome>,
}

impl CompressionManifest {
    fn empty() -> Self {
        Self {
            messages_total: 0,
            messages_below_frozen_floor: 0,
            latest_user_message_index: None,
            block_outcomes: Vec::new(),
        }
    }

    fn has_compressed_block(&self) -> bool {
        self.block_outcomes
            .iter()
            .any(|b| matches!(b.action, BlockAction::Compressed { .. }))
    }

    pub fn tokens_saved(&self) -> usize {
        self.block_outcomes
            .iter()
            .filter_map(|b| match &b.action {
                BlockAction::Compressed {
                    original_tokens,
                    compressed_tokens,
                    ..
                } => Some(original_tokens.saturating_sub(*compressed_tokens)),
                _ => None,
            })
            .sum()
    }

    pub fn transforms_applied(&self) -> Vec<&'static str> {
        let mut seen: Vec<&'static str> = Vec::new();
        for b in &self.block_outcomes {
            if let BlockAction::Compressed { strategy, .. } = &b.action {
                if !seen.contains(strategy) {
                    seen.push(*strategy);
                }
            }
        }
        seen
    }
}

pub fn summarize_openai_responses_no_change_reason(manifest: &CompressionManifest) -> &'static str {
    if manifest.block_outcomes.is_empty() {
        return "no_eligible_items";
    }

    let mut saw_no_compression_applied = false;
    let mut saw_excluded = false;
    let mut saw_below_output_floor = false;
    let mut saw_below_plain_text_floor = false;
    let mut saw_rejected_not_smaller = false;
    let mut saw_compressor_error = false;

    for outcome in &manifest.block_outcomes {
        match &outcome.action {
            BlockAction::CompressorError { .. } => saw_compressor_error = true,
            BlockAction::RejectedNotSmaller { .. } => saw_rejected_not_smaller = true,
            BlockAction::BelowByteThreshold { content_type, .. } => {
                if *content_type == "output_item" {
                    saw_below_output_floor = true;
                } else {
                    saw_below_plain_text_floor = true;
                }
            }
            BlockAction::NoCompressionApplied { .. } => saw_no_compression_applied = true,
            BlockAction::Excluded { .. } => saw_excluded = true,
            BlockAction::Compressed { .. } => {}
        }
    }

    if saw_compressor_error {
        "compressor_error"
    } else if saw_rejected_not_smaller {
        "rejected_not_smaller"
    } else if saw_below_output_floor {
        "below_output_floor"
    } else if saw_below_plain_text_floor {
        "below_plain_text_floor"
    } else if saw_excluded {
        "excluded_live_zone"
    } else if saw_no_compression_applied {
        "no_compressible_content"
    } else {
        "no_change"
    }
}

#[derive(Debug)]
pub enum LiveZoneOutcome {
    NoChange { manifest: CompressionManifest },
    Modified {
        new_body: Box<RawValue>,
        manifest: CompressionManifest,
    },
}

#[derive(Debug, Error)]
pub enum LiveZoneError {
    #[error("request body is not valid JSON: {0}")]
    BodyNotJson(serde_json::Error),
    #[error("body has no `messages` array")]
    NoMessagesArray,
}

const HOT_ZONE_BLOCK_TYPES: &[&str] = &[
    "tool_use",
    "thinking",
    "redacted_thinking",
    "compaction",
];


fn smart_crusher() -> &'static SmartCrusher {
    static INSTANCE: OnceLock<SmartCrusher> = OnceLock::new();
    INSTANCE.get_or_init(|| SmartCrusher::new(SmartCrusherConfig::default()))
}

fn log_compressor() -> &'static LogCompressor {
    static INSTANCE: OnceLock<LogCompressor> = OnceLock::new();
    INSTANCE.get_or_init(|| LogCompressor::new(LogCompressorConfig::default()))
}

fn search_compressor() -> &'static SearchCompressor {
    static INSTANCE: OnceLock<SearchCompressor> = OnceLock::new();
    INSTANCE.get_or_init(|| SearchCompressor::new(SearchCompressorConfig::default()))
}

fn diff_compressor() -> &'static DiffCompressor {
    static INSTANCE: OnceLock<DiffCompressor> = OnceLock::new();
    INSTANCE.get_or_init(|| DiffCompressor::new(DiffCompressorConfig::default()))
}

fn text_crusher() -> &'static TextCrusher {
    static INSTANCE: OnceLock<TextCrusher> = OnceLock::new();
    INSTANCE.get_or_init(|| TextCrusher::new(TextCrusherConfig::default()))
}


pub fn compress_anthropic_live_zone(
    body_raw: &[u8],
    frozen_message_count: usize,
    auth_mode: AuthMode,
    model: &str,
) -> Result<LiveZoneOutcome, LiveZoneError> {
    compress_anthropic_live_zone_with_ccr(body_raw, frozen_message_count, auth_mode, model, None)
}

pub fn compress_anthropic_live_zone_with_ccr(
    body_raw: &[u8],
    frozen_message_count: usize,
    _auth_mode: AuthMode,
    model: &str,
    ccr_store: Option<&dyn CcrStore>,
) -> Result<LiveZoneOutcome, LiveZoneError> {
    let parsed: Value = serde_json::from_slice(body_raw).map_err(LiveZoneError::BodyNotJson)?;
    let messages = parsed
        .get("messages")
        .and_then(Value::as_array)
        .ok_or(LiveZoneError::NoMessagesArray)?;

    if messages.is_empty() {
        return Ok(LiveZoneOutcome::NoChange {
            manifest: CompressionManifest::empty(),
        });
    }

    let messages_total = messages.len();
    let messages_below_frozen_floor = frozen_message_count.min(messages_total);

    let latest_user_message_index = find_latest_user_message_index(messages, frozen_message_count);

    let Some(target_idx) = latest_user_message_index else {
        return Ok(LiveZoneOutcome::NoChange {
            manifest: CompressionManifest {
                messages_total,
                messages_below_frozen_floor,
                latest_user_message_index: None,
                block_outcomes: Vec::new(),
            },
        });
    };

    let plan = match plan_block_replacements(body_raw, target_idx) {
        Ok(p) => p,
        Err(_) => {
            let block_outcomes =
                inspect_latest_user_blocks_value(&messages[target_idx], target_idx)
                    .unwrap_or_default();
            return Ok(LiveZoneOutcome::NoChange {
                manifest: CompressionManifest {
                    messages_total,
                    messages_below_frozen_floor,
                    latest_user_message_index: Some(target_idx),
                    block_outcomes,
                },
            });
        }
    };

    let mut block_outcomes: Vec<BlockOutcome> = Vec::with_capacity(plan.len());
    let mut replacements: Vec<Replacement> = Vec::new();
    let tokenizer = get_tokenizer(model);

    for slot in plan {
        let outcome = match slot.kind {
            SlotKind::HotZone(block_type) => BlockOutcome {
                message_index: target_idx,
                block_index: Some(slot.block_index),
                block_type,
                action: BlockAction::Excluded {
                    reason: ExclusionReason::HotZoneBlockType,
                },
            },
            SlotKind::Compressible {
                block_type,
                content_text,
                content_byte_range,
            } => {
                let detected = detect_content_type(&content_text);
                let outcome: BlockOutcome = compress_one_block(
                    &content_text,
                    detected.content_type,
                    content_byte_range,
                    target_idx,
                    Some(slot.block_index),
                    block_type,
                    tokenizer.as_ref(),
                    &mut replacements,
                    ccr_store,
                );
                outcome
            }
            SlotKind::StringContent {
                content_text,
                content_byte_range,
            } => {
                let detected = detect_content_type(&content_text);
                compress_one_block(
                    &content_text,
                    detected.content_type,
                    content_byte_range,
                    target_idx,
                    None,
                    "string_content".to_string(),
                    tokenizer.as_ref(),
                    &mut replacements,
                    ccr_store,
                )
            }
        };
        block_outcomes.push(outcome);
    }

    let manifest = CompressionManifest {
        messages_total,
        messages_below_frozen_floor,
        latest_user_message_index: Some(target_idx),
        block_outcomes,
    };

    if !manifest.has_compressed_block() || replacements.is_empty() {
        return Ok(LiveZoneOutcome::NoChange { manifest });
    }

    let new_bytes = apply_replacements(body_raw, &mut replacements);

    let new_body_str = match std::str::from_utf8(&new_bytes) {
        Ok(s) => s,
        Err(_) => {
            return Ok(LiveZoneOutcome::NoChange { manifest });
        }
    };
    let raw = match RawValue::from_string(new_body_str.to_string()) {
        Ok(r) => r,
        Err(_) => {
            return Ok(LiveZoneOutcome::NoChange { manifest });
        }
    };

    Ok(LiveZoneOutcome::Modified {
        new_body: raw,
        manifest,
    })
}


#[allow(clippy::too_many_arguments)]
fn compress_one_block(
    content_text: &str,
    content_type: ContentType,
    content_byte_range: (usize, usize),
    message_index: usize,
    block_index: Option<usize>,
    block_type: String,
    tokenizer: &dyn crate::tokenizer::Tokenizer,
    replacements: &mut Vec<Replacement>,
    ccr_store: Option<&dyn CcrStore>,
) -> BlockOutcome {
    if !content_text.is_empty() && content_text.len() < threshold_for(content_type) {
        return BlockOutcome {
            message_index,
            block_index,
            block_type,
            action: BlockAction::BelowByteThreshold {
                content_type: content_type.as_str(),
                byte_count: content_text.len(),
                threshold_bytes: threshold_for(content_type),
            },
        };
    }

    match dispatch_compressor(content_text, content_type) {
        DispatchResult::NoOp { content_type } => BlockOutcome {
            message_index,
            block_index,
            block_type,
            action: BlockAction::NoCompressionApplied {
                content_type: content_type.to_string(),
            },
        },
        DispatchResult::Compressed {
            strategy,
            compressed,
        } => {
            let original_bytes = content_text.len();
            let (compressed_for_replacement, ccr_hash_emitted) =
                maybe_inject_ccr_marker(content_text, &compressed, ccr_store);
            let compressed_bytes = compressed_for_replacement.len();
            let original_tokens = tokenizer.count_text(content_text);
            let compressed_tokens = tokenizer.count_text(&compressed_for_replacement);
            if compressed_tokens >= original_tokens {
                BlockOutcome {
                    message_index,
                    block_index,
                    block_type,
                    action: BlockAction::RejectedNotSmaller {
                        strategy,
                        original_bytes,
                        compressed_bytes,
                        original_tokens,
                        compressed_tokens,
                    },
                }
            } else {
                if let (Some(store), Some(hash)) = (ccr_store, ccr_hash_emitted.as_deref()) {
                    store.put(hash, content_text);
                }
                let replacement_bytes = serde_json::to_vec(&compressed_for_replacement)
                    .expect("string is always JSON-encodable");
                replacements.push(Replacement {
                    range: content_byte_range,
                    replacement: replacement_bytes,
                });
                BlockOutcome {
                    message_index,
                    block_index,
                    block_type,
                    action: BlockAction::Compressed {
                        strategy,
                        original_bytes,
                        compressed_bytes,
                        original_tokens,
                        compressed_tokens,
                    },
                }
            }
        }
        DispatchResult::Error { strategy, error } => BlockOutcome {
            message_index,
            block_index,
            block_type,
            action: BlockAction::CompressorError { strategy, error },
        },
    }
}

fn find_latest_user_message_index(messages: &[Value], floor: usize) -> Option<usize> {
    let start = floor.min(messages.len());
    for (offset, msg) in messages.iter().enumerate().rev() {
        if offset < start {
            return None;
        }
        if msg.get("role").and_then(Value::as_str) == Some("user") {
            return Some(offset);
        }
    }
    None
}

#[derive(Deserialize)]
struct BodyView<'a> {
    #[serde(borrow)]
    messages: Vec<&'a RawValue>,
}

#[derive(Deserialize)]
struct MessageView<'a> {
    #[serde(borrow, default)]
    content: Option<&'a RawValue>,
}

#[derive(Deserialize)]
struct BlockHeader<'a> {
    #[serde(borrow, default)]
    r#type: Option<&'a str>,
    #[serde(borrow, default)]
    content: Option<&'a RawValue>,
}

struct PlanSlot {
    block_index: usize,
    kind: SlotKind,
}

enum SlotKind {
    Compressible {
        block_type: String,
        content_text: String,
        content_byte_range: (usize, usize),
    },
    StringContent {
        content_text: String,
        content_byte_range: (usize, usize),
    },
    HotZone(String),
}

fn block_has_string_text_field(block_json: &str) -> bool {
    #[derive(Deserialize)]
    struct Probe<'a> {
        #[serde(borrow, default)]
        text: Option<&'a RawValue>,
    }
    serde_json::from_str::<Probe<'_>>(block_json)
        .ok()
        .and_then(|p| p.text)
        .is_some_and(|t| t.get().trim_start().starts_with('"'))
}

fn plan_block_replacements(
    body_raw: &[u8],
    target_msg_idx: usize,
) -> Result<Vec<PlanSlot>, PlanError> {
    let body_str = std::str::from_utf8(body_raw).map_err(|_| PlanError::ParseFailed)?;
    let body: BodyView<'_> = serde_json::from_str(body_str).map_err(|_| PlanError::ParseFailed)?;
    let target_msg_raw = body
        .messages
        .get(target_msg_idx)
        .ok_or(PlanError::TargetOutOfBounds)?;

    let msg_view: MessageView<'_> =
        serde_json::from_str(target_msg_raw.get()).map_err(|_| PlanError::ParseFailed)?;

    let Some(content_raw) = msg_view.content else {
        return Ok(Vec::new());
    };

    let content_offset_in_msg =
        bytes_offset_of(target_msg_raw.get(), content_raw.get()).ok_or(PlanError::OffsetMissing)?;
    let msg_offset_in_body =
        bytes_offset_of(body_str, target_msg_raw.get()).ok_or(PlanError::OffsetMissing)?;
    let content_offset_in_body = msg_offset_in_body + content_offset_in_msg;

    let content_str = content_raw.get();

    if content_str.starts_with('"') {
        let unescaped: String =
            serde_json::from_str(content_str).map_err(|_| PlanError::ParseFailed)?;
        return Ok(vec![PlanSlot {
            block_index: 0,
            kind: SlotKind::StringContent {
                content_text: unescaped,
                content_byte_range: (
                    content_offset_in_body,
                    content_offset_in_body + content_str.len(),
                ),
            },
        }]);
    }

    let blocks: Vec<&RawValue> =
        serde_json::from_str(content_str).map_err(|_| PlanError::ParseFailed)?;

    let mut slots = Vec::with_capacity(blocks.len());
    for (block_idx, block_raw) in blocks.iter().enumerate() {
        let block_offset_in_content =
            bytes_offset_of(content_str, block_raw.get()).ok_or(PlanError::OffsetMissing)?;
        let block_offset_in_body = content_offset_in_body + block_offset_in_content;

        let header: BlockHeader<'_> =
            serde_json::from_str(block_raw.get()).map_err(|_| PlanError::ParseFailed)?;
        let block_type = match header.r#type {
            Some(t) => t.to_string(),
            None if block_has_string_text_field(block_raw.get()) => "text".to_string(),
            None => "unknown".to_string(),
        };

        if HOT_ZONE_BLOCK_TYPES.iter().any(|t| *t == block_type) {
            slots.push(PlanSlot {
                block_index: block_idx,
                kind: SlotKind::HotZone(block_type),
            });
            continue;
        }

        let (inner_field_str, inner_field_offset_in_block) = match block_type.as_str() {
            "tool_result" => {
                let Some(field_raw) = header.content else {
                    slots.push(PlanSlot {
                        block_index: block_idx,
                        kind: SlotKind::Compressible {
                            block_type,
                            content_text: String::new(),
                            content_byte_range: (block_offset_in_body, block_offset_in_body),
                        },
                    });
                    continue;
                };
                let off = bytes_offset_of(block_raw.get(), field_raw.get())
                    .ok_or(PlanError::OffsetMissing)?;
                (field_raw.get(), off)
            }
            "text" => {
                #[derive(Deserialize)]
                struct TextHeader<'a> {
                    #[serde(borrow, default)]
                    text: Option<&'a RawValue>,
                }
                let h: TextHeader<'_> =
                    serde_json::from_str(block_raw.get()).map_err(|_| PlanError::ParseFailed)?;
                let Some(text_raw) = h.text else {
                    slots.push(PlanSlot {
                        block_index: block_idx,
                        kind: SlotKind::Compressible {
                            block_type,
                            content_text: String::new(),
                            content_byte_range: (block_offset_in_body, block_offset_in_body),
                        },
                    });
                    continue;
                };
                let off = bytes_offset_of(block_raw.get(), text_raw.get())
                    .ok_or(PlanError::OffsetMissing)?;
                (text_raw.get(), off)
            }
            _ => {
                slots.push(PlanSlot {
                    block_index: block_idx,
                    kind: SlotKind::Compressible {
                        block_type,
                        content_text: String::new(),
                        content_byte_range: (block_offset_in_body, block_offset_in_body),
                    },
                });
                continue;
            }
        };

        if !inner_field_str.starts_with('"') {
            slots.push(PlanSlot {
                block_index: block_idx,
                kind: SlotKind::Compressible {
                    block_type,
                    content_text: String::new(),
                    content_byte_range: (block_offset_in_body, block_offset_in_body),
                },
            });
            continue;
        }
        let unescaped: String =
            serde_json::from_str(inner_field_str).map_err(|_| PlanError::ParseFailed)?;

        let inner_field_start_in_body = block_offset_in_body + inner_field_offset_in_block;
        let inner_field_end_in_body = inner_field_start_in_body + inner_field_str.len();

        slots.push(PlanSlot {
            block_index: block_idx,
            kind: SlotKind::Compressible {
                block_type,
                content_text: unescaped,
                content_byte_range: (inner_field_start_in_body, inner_field_end_in_body),
            },
        });
    }

    Ok(slots)
}

#[derive(Debug)]
enum PlanError {
    ParseFailed,
    OffsetMissing,
    TargetOutOfBounds,
}

fn bytes_offset_of(parent: &str, child: &str) -> Option<usize> {
    let parent_start = parent.as_ptr() as usize;
    let parent_end = parent_start + parent.len();
    let child_start = child.as_ptr() as usize;
    if child_start < parent_start || child_start + child.len() > parent_end {
        return None;
    }
    Some(child_start - parent_start)
}

struct Replacement {
    range: (usize, usize),
    replacement: Vec<u8>,
}

fn apply_replacements(original: &[u8], replacements: &mut [Replacement]) -> Vec<u8> {
    replacements.sort_by_key(|r| r.range.0);

    let removed: usize = replacements.iter().map(|r| r.range.1 - r.range.0).sum();
    let added: usize = replacements.iter().map(|r| r.replacement.len()).sum();
    let mut out = Vec::with_capacity(original.len().saturating_sub(removed) + added);

    let mut cursor = 0usize;
    for r in replacements.iter() {
        out.extend_from_slice(&original[cursor..r.range.0]);
        out.extend_from_slice(&r.replacement);
        cursor = r.range.1;
    }
    out.extend_from_slice(&original[cursor..]);
    out
}

fn maybe_inject_ccr_marker(
    original: &str,
    compressed: &str,
    ccr_store: Option<&dyn CcrStore>,
) -> (String, Option<String>) {
    if ccr_store.is_none() {
        return (compressed.to_string(), None);
    }
    let hash = compute_key(original.as_bytes());
    let marker = marker_for(&hash);
    let augmented = if compressed.ends_with('\n') {
        format!("{compressed}{marker}")
    } else {
        format!("{compressed}\n{marker}")
    };
    (augmented, Some(hash))
}

enum DispatchResult {
    NoOp { content_type: &'static str },
    Compressed {
        strategy: &'static str,
        compressed: String,
    },
    #[allow(dead_code)]
    Error {
        strategy: &'static str,
        error: String,
    },
}

fn dispatch_compressor(text: &str, content_type: ContentType) -> DispatchResult {
    if text.is_empty() {
        return DispatchResult::NoOp {
            content_type: content_type.as_str(),
        };
    }

    match content_type {
        ContentType::JsonArray => {
            let result = smart_crusher().crush(text, EMPTY_QUERY, DEFAULT_BIAS);
            if !result.was_modified {
                return DispatchResult::NoOp {
                    content_type: content_type.as_str(),
                };
            }
            DispatchResult::Compressed {
                strategy: STRATEGY_SMART_CRUSHER,
                compressed: result.compressed,
            }
        }
        ContentType::BuildOutput => {
            let (result, _stats) = log_compressor().compress(text, DEFAULT_BIAS);
            if result.compressed == result.original {
                return DispatchResult::NoOp {
                    content_type: content_type.as_str(),
                };
            }
            DispatchResult::Compressed {
                strategy: STRATEGY_LOG_COMPRESSOR,
                compressed: result.compressed,
            }
        }
        ContentType::SearchResults => {
            let (result, _stats) = search_compressor().compress(text, EMPTY_QUERY, DEFAULT_BIAS);
            if result.compressed == result.original {
                return DispatchResult::NoOp {
                    content_type: content_type.as_str(),
                };
            }
            DispatchResult::Compressed {
                strategy: STRATEGY_SEARCH_COMPRESSOR,
                compressed: result.compressed,
            }
        }
        ContentType::GitDiff => {
            let result = diff_compressor().compress(text, EMPTY_QUERY);
            if result.compressed == text {
                return DispatchResult::NoOp {
                    content_type: content_type.as_str(),
                };
            }
            DispatchResult::Compressed {
                strategy: STRATEGY_DIFF_COMPRESSOR,
                compressed: result.compressed,
            }
        }
        ContentType::SourceCode => {
            let result = text_crusher().compress(text, EMPTY_QUERY, None);
            if result.compressed == text {
                return DispatchResult::NoOp {
                    content_type: content_type.as_str(),
                };
            }
            DispatchResult::Compressed {
                strategy: STRATEGY_TEXT_CRUSHER,
                compressed: result.compressed,
            }
        }
        ContentType::PlainText => {
            let result = text_crusher().compress(text, EMPTY_QUERY, None);
            if result.compressed == text {
                return DispatchResult::NoOp {
                    content_type: content_type.as_str(),
                };
            }
            DispatchResult::Compressed {
                strategy: STRATEGY_TEXT_CRUSHER,
                compressed: result.compressed,
            }
        },
        ContentType::Html => DispatchResult::NoOp {
            content_type: content_type.as_str(),
        },
    }
}

fn inspect_latest_user_blocks_value(
    message: &Value,
    message_index: usize,
) -> Option<Vec<BlockOutcome>> {
    let content = message.get("content")?;

    if content.as_str().is_some() {
        return Some(vec![BlockOutcome {
            message_index,
            block_index: None,
            block_type: "string_content".to_string(),
            action: BlockAction::NoCompressionApplied {
                content_type: "text".to_string(),
            },
        }]);
    }

    let blocks = content.as_array()?;
    let mut outcomes = Vec::with_capacity(blocks.len());
    for (idx, block) in blocks.iter().enumerate() {
        let block_type = block
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_string();
        let action = if HOT_ZONE_BLOCK_TYPES.iter().any(|t| *t == block_type) {
            BlockAction::Excluded {
                reason: ExclusionReason::HotZoneBlockType,
            }
        } else {
            BlockAction::NoCompressionApplied {
                content_type: "unknown".to_string(),
            }
        };
        outcomes.push(BlockOutcome {
            message_index,
            block_index: Some(idx),
            block_type,
            action,
        });
    }
    Some(outcomes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn body(value: Value) -> Vec<u8> {
        serde_json::to_vec(&value).unwrap()
    }

    fn outcome_block_actions(o: &LiveZoneOutcome) -> Vec<&BlockAction> {
        let manifest = match o {
            LiveZoneOutcome::NoChange { manifest } => manifest,
            LiveZoneOutcome::Modified { manifest, .. } => manifest,
        };
        manifest.block_outcomes.iter().map(|b| &b.action).collect()
    }

    #[test]
    fn empty_messages_yields_no_change() {
        let b = body(json!({"model": "claude", "messages": []}));
        let out = compress_anthropic_live_zone(&b, 0, AuthMode::Payg, DEFAULT_MODEL).unwrap();
        match out {
            LiveZoneOutcome::NoChange { manifest } => {
                assert_eq!(manifest.messages_total, 0);
                assert_eq!(manifest.latest_user_message_index, None);
                assert!(manifest.block_outcomes.is_empty());
            }
            _ => panic!("expected NoChange"),
        }
    }

    #[test]
    fn no_messages_field_errors() {
        let b = body(json!({"model": "claude"}));
        let err = compress_anthropic_live_zone(&b, 0, AuthMode::Payg, DEFAULT_MODEL).unwrap_err();
        assert!(matches!(err, LiveZoneError::NoMessagesArray));
    }

    #[test]
    fn invalid_json_errors() {
        let err = compress_anthropic_live_zone(b"not json", 0, AuthMode::Payg, DEFAULT_MODEL)
            .unwrap_err();
        assert!(matches!(err, LiveZoneError::BodyNotJson(_)));
    }

    #[test]
    fn dispatches_only_to_latest_user_message() {
        let b = body(json!({
            "messages": [
                {"role": "user", "content": "first user"},
                {"role": "assistant", "content": "first asst"},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "result"},
                    {"type": "text", "text": "summarize"}
                ]},
            ]
        }));
        let out = compress_anthropic_live_zone(&b, 0, AuthMode::Payg, DEFAULT_MODEL).unwrap();
        let manifest = match &out {
            LiveZoneOutcome::NoChange { manifest } => manifest,
            LiveZoneOutcome::Modified { manifest, .. } => manifest,
        };
        assert_eq!(manifest.latest_user_message_index, Some(2));
        let block_msg_indices: Vec<usize> = manifest
            .block_outcomes
            .iter()
            .map(|b| b.message_index)
            .collect();
        assert!(
            block_msg_indices.iter().all(|i| *i == 2),
            "all block outcomes must reference the latest user message; got {block_msg_indices:?}"
        );
    }

    #[test]
    fn respects_frozen_message_count() {
        let b = body(json!({
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "user", "content": [{"type": "text", "text": "second"}]},
            ]
        }));
        let out = compress_anthropic_live_zone(&b, 2, AuthMode::Payg, DEFAULT_MODEL).unwrap();
        let manifest = match &out {
            LiveZoneOutcome::NoChange { manifest } => manifest,
            _ => panic!("expected NoChange"),
        };
        assert_eq!(manifest.latest_user_message_index, None);
        assert!(manifest.block_outcomes.is_empty());
        assert_eq!(manifest.messages_below_frozen_floor, 2);
    }

    #[test]
    fn excludes_hot_zone_block_types() {
        let b = body(json!({
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t", "content": "x"},
                    {"type": "thinking", "thinking": "...", "signature": "sig"},
                    {"type": "text", "text": "ok"},
                ]
            }]
        }));
        let out = compress_anthropic_live_zone(&b, 0, AuthMode::Payg, DEFAULT_MODEL).unwrap();
        let actions = outcome_block_actions(&out);
        assert_eq!(actions.len(), 3);
        assert!(matches!(actions[0], BlockAction::BelowByteThreshold { .. }));
        assert!(matches!(
            actions[1],
            BlockAction::Excluded {
                reason: ExclusionReason::HotZoneBlockType
            }
        ));
        assert!(matches!(actions[2], BlockAction::BelowByteThreshold { .. }));
    }

    #[test]
    fn string_content_message_records_synthetic_block() {
        let b = body(json!({
            "messages": [{"role": "user", "content": "just a string"}]
        }));
        let out = compress_anthropic_live_zone(&b, 0, AuthMode::Payg, DEFAULT_MODEL).unwrap();
        let manifest = match &out {
            LiveZoneOutcome::NoChange { manifest } => manifest,
            LiveZoneOutcome::Modified { manifest, .. } => manifest,
        };
        assert_eq!(manifest.block_outcomes.len(), 1);
        assert_eq!(manifest.block_outcomes[0].block_type, "string_content");
        assert!(matches!(
            manifest.block_outcomes[0].action,
            BlockAction::BelowByteThreshold { .. }
        ));
    }

    #[test]
    fn no_user_message_in_live_zone_returns_no_blocks() {
        let b = body(json!({
            "messages": [{"role": "assistant", "content": "hi"}]
        }));
        let out = compress_anthropic_live_zone(&b, 0, AuthMode::Payg, DEFAULT_MODEL).unwrap();
        let manifest = match &out {
            LiveZoneOutcome::NoChange { manifest } => manifest,
            _ => panic!("expected NoChange"),
        };
        assert_eq!(manifest.latest_user_message_index, None);
        assert!(manifest.block_outcomes.is_empty());
    }

    #[test]
    fn auth_mode_does_not_affect_b3_outcome_for_short_input() {
        let b = body(json!({
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        }));
        let payg = compress_anthropic_live_zone(&b, 0, AuthMode::Payg, DEFAULT_MODEL).unwrap();
        let oauth = compress_anthropic_live_zone(&b, 0, AuthMode::OAuth, DEFAULT_MODEL).unwrap();
        let sub =
            compress_anthropic_live_zone(&b, 0, AuthMode::Subscription, DEFAULT_MODEL).unwrap();
        for o in [&payg, &oauth, &sub] {
            assert!(matches!(o, LiveZoneOutcome::NoChange { .. }));
        }
    }

    #[test]
    fn no_change_when_input_already_minimal_returns_original_semantics() {
        let b = body(json!({
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t", "content": "x"},
                ]
            }]
        }));
        let out = compress_anthropic_live_zone(&b, 0, AuthMode::Payg, DEFAULT_MODEL).unwrap();
        assert!(matches!(out, LiveZoneOutcome::NoChange { .. }));
    }

    #[test]
    fn block_has_string_text_field_detects_converse_text_only() {
        assert!(block_has_string_text_field(r#"{"text":"hello"}"#));
        assert!(!block_has_string_text_field(
            r#"{"image":{"format":"png"}}"#
        ));
        assert!(!block_has_string_text_field(r#"{"toolUse":{"name":"x"}}"#));
        assert!(!block_has_string_text_field(r#"{"text":["a"]}"#));
        assert!(!block_has_string_text_field(r#"{"text":{"v":1}}"#));
    }

    #[test]
    fn converse_typeless_text_block_routes_like_anthropic_text() {
        let payload = "{\"k\": \"v\", \"n\": 1}\n".repeat(200);
        let converse = body(json!({
            "messages": [{"role": "user", "content": [{"text": payload}]}]
        }));
        let anthropic = body(json!({
            "messages": [{"role": "user", "content": [{"type": "text", "text": payload}]}]
        }));
        let c = compress_anthropic_live_zone(&converse, 0, AuthMode::Payg, DEFAULT_MODEL).unwrap();
        let a = compress_anthropic_live_zone(&anthropic, 0, AuthMode::Payg, DEFAULT_MODEL).unwrap();

        assert_eq!(
            std::mem::discriminant(&c),
            std::mem::discriminant(&a),
            "converse text block must dispatch like an anthropic text block"
        );
        let cm = match &c {
            LiveZoneOutcome::NoChange { manifest } => manifest,
            LiveZoneOutcome::Modified { manifest, .. } => manifest,
        };
        let am = match &a {
            LiveZoneOutcome::NoChange { manifest } => manifest,
            LiveZoneOutcome::Modified { manifest, .. } => manifest,
        };
        assert_eq!(cm.block_outcomes.len(), 1);
        assert_eq!(am.block_outcomes.len(), 1);
        assert_eq!(
            cm.block_outcomes[0].block_type,
            am.block_outcomes[0].block_type
        );
        assert_eq!(cm.block_outcomes[0].block_type, "text");
    }

    #[test]
    fn manifest_records_messages_below_floor() {
        let b = body(json!({
            "messages": [
                {"role": "user", "content": "frozen"},
                {"role": "assistant", "content": "frozen"},
                {"role": "user", "content": "live"},
            ]
        }));
        let out = compress_anthropic_live_zone(&b, 2, AuthMode::Payg, DEFAULT_MODEL).unwrap();
        let manifest = match &out {
            LiveZoneOutcome::NoChange { manifest } => manifest,
            LiveZoneOutcome::Modified { manifest, .. } => manifest,
        };
        assert_eq!(manifest.messages_total, 3);
        assert_eq!(manifest.messages_below_frozen_floor, 2);
        assert_eq!(manifest.latest_user_message_index, Some(2));
    }

    #[test]
    fn frozen_count_above_messages_clamps() {
        let b = body(json!({
            "messages": [{"role": "user", "content": "x"}]
        }));
        let out = compress_anthropic_live_zone(&b, 99, AuthMode::Payg, DEFAULT_MODEL).unwrap();
        let manifest = match &out {
            LiveZoneOutcome::NoChange { manifest } => manifest,
            _ => panic!("expected NoChange"),
        };
        assert_eq!(manifest.messages_below_frozen_floor, 1);
        assert_eq!(manifest.latest_user_message_index, None);
    }


    fn make_manifest(actions: Vec<BlockAction>) -> CompressionManifest {
        CompressionManifest {
            messages_total: actions.len(),
            messages_below_frozen_floor: 0,
            latest_user_message_index: None,
            block_outcomes: actions
                .into_iter()
                .enumerate()
                .map(|(i, a)| BlockOutcome {
                    message_index: i,
                    block_index: None,
                    block_type: "test".to_string(),
                    action: a,
                })
                .collect(),
        }
    }

    #[test]
    fn tokens_saved_zero_for_empty_manifest() {
        let m = CompressionManifest::empty();
        assert_eq!(m.tokens_saved(), 0);
        assert!(m.transforms_applied().is_empty());
    }

    #[test]
    fn tokens_saved_sums_compressed_outcomes_only() {
        let m = make_manifest(vec![
            BlockAction::Compressed {
                strategy: "smart_crusher",
                original_bytes: 0,
                compressed_bytes: 0,
                original_tokens: 100,
                compressed_tokens: 30,
            },
            BlockAction::NoCompressionApplied {
                content_type: "image".to_string(),
            },
            BlockAction::Compressed {
                strategy: "log_compressor",
                original_bytes: 0,
                compressed_bytes: 0,
                original_tokens: 200,
                compressed_tokens: 50,
            },
            BlockAction::RejectedNotSmaller {
                strategy: "smart_crusher",
                original_bytes: 0,
                compressed_bytes: 0,
                original_tokens: 80,
                compressed_tokens: 90,
            },
        ]);
        assert_eq!(m.tokens_saved(), 220);
    }

    #[test]
    fn transforms_applied_dedup_first_seen_order() {
        let m = make_manifest(vec![
            BlockAction::Compressed {
                strategy: "log_compressor",
                original_bytes: 0,
                compressed_bytes: 0,
                original_tokens: 50,
                compressed_tokens: 10,
            },
            BlockAction::Compressed {
                strategy: "smart_crusher",
                original_bytes: 0,
                compressed_bytes: 0,
                original_tokens: 50,
                compressed_tokens: 10,
            },
            BlockAction::Compressed {
                strategy: "log_compressor",
                original_bytes: 0,
                compressed_bytes: 0,
                original_tokens: 50,
                compressed_tokens: 10,
            },
        ]);
        assert_eq!(
            m.transforms_applied(),
            vec!["log_compressor", "smart_crusher"]
        );
    }

    #[test]
    fn tokens_saved_saturates_when_compressed_exceeds_original() {
        let m = make_manifest(vec![BlockAction::Compressed {
            strategy: "smart_crusher",
            original_bytes: 0,
            compressed_bytes: 0,
            original_tokens: 10,
            compressed_tokens: 50,
        }]);
        assert_eq!(m.tokens_saved(), 0);
    }
}


pub fn compress_openai_chat_live_zone(
    body_raw: &[u8],
    _auth_mode: AuthMode,
    model: &str,
) -> Result<LiveZoneOutcome, LiveZoneError> {
    let parsed: Value = serde_json::from_slice(body_raw).map_err(LiveZoneError::BodyNotJson)?;
    let messages = parsed
        .get("messages")
        .and_then(Value::as_array)
        .ok_or(LiveZoneError::NoMessagesArray)?;

    if messages.is_empty() {
        return Ok(LiveZoneOutcome::NoChange {
            manifest: CompressionManifest::empty(),
        });
    }

    let messages_total = messages.len();

    let latest_tool_idx = find_latest_role_index(messages, "tool");
    let latest_user_idx = find_latest_role_index(messages, "user");

    if latest_tool_idx.is_none() && latest_user_idx.is_none() {
        return Ok(LiveZoneOutcome::NoChange {
            manifest: CompressionManifest {
                messages_total,
                messages_below_frozen_floor: 0,
                latest_user_message_index: latest_user_idx,
                block_outcomes: Vec::new(),
            },
        });
    }

    let mut all_slots: Vec<(usize, OpenAiPlanSlot)> = Vec::new();
    if let Some(idx) = latest_tool_idx {
        if let Ok(slot) = plan_openai_tool_message(body_raw, idx) {
            all_slots.push((idx, slot));
        }
    }
    if let Some(idx) = latest_user_idx {
        if let Ok(slots) = plan_openai_user_message(body_raw, idx) {
            for s in slots {
                all_slots.push((idx, s));
            }
        }
    }

    if all_slots.is_empty() {
        return Ok(LiveZoneOutcome::NoChange {
            manifest: CompressionManifest {
                messages_total,
                messages_below_frozen_floor: 0,
                latest_user_message_index: latest_user_idx,
                block_outcomes: Vec::new(),
            },
        });
    }

    let tokenizer = get_tokenizer(model);
    let mut block_outcomes: Vec<BlockOutcome> = Vec::with_capacity(all_slots.len());
    let mut replacements: Vec<Replacement> = Vec::new();

    for (msg_idx, slot) in all_slots {
        let detected = detect_content_type(&slot.content_text);
        let outcome = compress_one_block(
            &slot.content_text,
            detected.content_type,
            slot.content_byte_range,
            msg_idx,
            slot.block_index,
            slot.block_type,
            tokenizer.as_ref(),
            &mut replacements,
            None,
        );
        block_outcomes.push(outcome);
    }

    let manifest = CompressionManifest {
        messages_total,
        messages_below_frozen_floor: 0,
        latest_user_message_index: latest_user_idx,
        block_outcomes,
    };

    if !manifest.has_compressed_block() || replacements.is_empty() {
        return Ok(LiveZoneOutcome::NoChange { manifest });
    }

    let new_bytes = apply_replacements(body_raw, &mut replacements);
    let new_body_str = match std::str::from_utf8(&new_bytes) {
        Ok(s) => s,
        Err(_) => return Ok(LiveZoneOutcome::NoChange { manifest }),
    };
    let raw = match RawValue::from_string(new_body_str.to_string()) {
        Ok(r) => r,
        Err(_) => return Ok(LiveZoneOutcome::NoChange { manifest }),
    };

    Ok(LiveZoneOutcome::Modified {
        new_body: raw,
        manifest,
    })
}

fn find_latest_role_index(messages: &[Value], role: &str) -> Option<usize> {
    for (idx, msg) in messages.iter().enumerate().rev() {
        if msg.get("role").and_then(Value::as_str) == Some(role) {
            return Some(idx);
        }
    }
    None
}

struct OpenAiPlanSlot {
    block_index: Option<usize>,
    block_type: String,
    content_text: String,
    content_byte_range: (usize, usize),
}

fn plan_openai_tool_message(body_raw: &[u8], msg_idx: usize) -> Result<OpenAiPlanSlot, PlanError> {
    let body_str = std::str::from_utf8(body_raw).map_err(|_| PlanError::ParseFailed)?;
    let body: BodyView<'_> = serde_json::from_str(body_str).map_err(|_| PlanError::ParseFailed)?;
    let msg_raw = body
        .messages
        .get(msg_idx)
        .ok_or(PlanError::TargetOutOfBounds)?;

    let msg_view: MessageView<'_> =
        serde_json::from_str(msg_raw.get()).map_err(|_| PlanError::ParseFailed)?;
    let content_raw = msg_view.content.ok_or(PlanError::ParseFailed)?;

    let content_offset_in_msg =
        bytes_offset_of(msg_raw.get(), content_raw.get()).ok_or(PlanError::OffsetMissing)?;
    let msg_offset_in_body =
        bytes_offset_of(body_str, msg_raw.get()).ok_or(PlanError::OffsetMissing)?;
    let content_offset_in_body = msg_offset_in_body + content_offset_in_msg;

    let content_str = content_raw.get();
    if !content_str.starts_with('"') {
        return Err(PlanError::ParseFailed);
    }

    let unescaped: String =
        serde_json::from_str(content_str).map_err(|_| PlanError::ParseFailed)?;

    Ok(OpenAiPlanSlot {
        block_index: None,
        block_type: "tool_content".to_string(),
        content_text: unescaped,
        content_byte_range: (
            content_offset_in_body,
            content_offset_in_body + content_str.len(),
        ),
    })
}

fn plan_openai_user_message(
    body_raw: &[u8],
    msg_idx: usize,
) -> Result<Vec<OpenAiPlanSlot>, PlanError> {
    let body_str = std::str::from_utf8(body_raw).map_err(|_| PlanError::ParseFailed)?;
    let body: BodyView<'_> = serde_json::from_str(body_str).map_err(|_| PlanError::ParseFailed)?;
    let msg_raw = body
        .messages
        .get(msg_idx)
        .ok_or(PlanError::TargetOutOfBounds)?;

    let msg_view: MessageView<'_> =
        serde_json::from_str(msg_raw.get()).map_err(|_| PlanError::ParseFailed)?;
    let Some(content_raw) = msg_view.content else {
        return Ok(Vec::new());
    };

    let content_offset_in_msg =
        bytes_offset_of(msg_raw.get(), content_raw.get()).ok_or(PlanError::OffsetMissing)?;
    let msg_offset_in_body =
        bytes_offset_of(body_str, msg_raw.get()).ok_or(PlanError::OffsetMissing)?;
    let content_offset_in_body = msg_offset_in_body + content_offset_in_msg;

    let content_str = content_raw.get();

    if content_str.starts_with('"') {
        let unescaped: String =
            serde_json::from_str(content_str).map_err(|_| PlanError::ParseFailed)?;
        return Ok(vec![OpenAiPlanSlot {
            block_index: None,
            block_type: "user_string".to_string(),
            content_text: unescaped,
            content_byte_range: (
                content_offset_in_body,
                content_offset_in_body + content_str.len(),
            ),
        }]);
    }

    let parts: Vec<&RawValue> =
        serde_json::from_str(content_str).map_err(|_| PlanError::ParseFailed)?;

    let mut slots = Vec::with_capacity(parts.len());
    for (part_idx, part_raw) in parts.iter().enumerate() {
        let header: BlockHeader<'_> =
            serde_json::from_str(part_raw.get()).map_err(|_| PlanError::ParseFailed)?;
        let block_type = header.r#type.unwrap_or("unknown").to_string();
        if block_type != "text" {
            continue;
        }

        #[derive(Deserialize)]
        struct TextHeader<'a> {
            #[serde(borrow, default)]
            text: Option<&'a RawValue>,
        }
        let h: TextHeader<'_> =
            serde_json::from_str(part_raw.get()).map_err(|_| PlanError::ParseFailed)?;
        let Some(text_raw) = h.text else {
            continue;
        };

        let part_offset_in_content =
            bytes_offset_of(content_str, part_raw.get()).ok_or(PlanError::OffsetMissing)?;
        let part_offset_in_body = content_offset_in_body + part_offset_in_content;
        let text_offset_in_part =
            bytes_offset_of(part_raw.get(), text_raw.get()).ok_or(PlanError::OffsetMissing)?;

        let text_str = text_raw.get();
        if !text_str.starts_with('"') {
            continue;
        }
        let unescaped: String =
            serde_json::from_str(text_str).map_err(|_| PlanError::ParseFailed)?;

        let text_start_in_body = part_offset_in_body + text_offset_in_part;
        let text_end_in_body = text_start_in_body + text_str.len();

        slots.push(OpenAiPlanSlot {
            block_index: Some(part_idx),
            block_type: "user_text".to_string(),
            content_text: unescaped,
            content_byte_range: (text_start_in_body, text_end_in_body),
        });
    }

    Ok(slots)
}

#[cfg(test)]
mod openai_chat_tests {
    use super::*;
    use serde_json::json;

    fn body(value: Value) -> Vec<u8> {
        serde_json::to_vec(&value).unwrap()
    }

    #[test]
    fn empty_messages_yields_no_change() {
        let b = body(json!({"model": "gpt-4o", "messages": []}));
        let out = compress_openai_chat_live_zone(&b, AuthMode::Payg, DEFAULT_MODEL).unwrap();
        assert!(matches!(out, LiveZoneOutcome::NoChange { .. }));
    }

    #[test]
    fn no_messages_field_errors() {
        let b = body(json!({"model": "gpt-4o"}));
        let err = compress_openai_chat_live_zone(&b, AuthMode::Payg, DEFAULT_MODEL).unwrap_err();
        assert!(matches!(err, LiveZoneError::NoMessagesArray));
    }

    #[test]
    fn invalid_json_errors() {
        let err =
            compress_openai_chat_live_zone(b"not json", AuthMode::Payg, DEFAULT_MODEL).unwrap_err();
        assert!(matches!(err, LiveZoneError::BodyNotJson(_)));
    }

    #[test]
    fn no_user_or_tool_yields_no_change() {
        let b = body(json!({
            "messages": [{"role": "system", "content": "you are helpful"}]
        }));
        let out = compress_openai_chat_live_zone(&b, AuthMode::Payg, "gpt-4o").unwrap();
        assert!(matches!(out, LiveZoneOutcome::NoChange { .. }));
    }

    #[test]
    fn tiny_tool_content_below_threshold_no_change() {
        let b = body(json!({
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "doing tool"},
                {"role": "tool", "tool_call_id": "t1", "content": "ok"},
            ]
        }));
        let out = compress_openai_chat_live_zone(&b, AuthMode::Payg, "gpt-4o").unwrap();
        match &out {
            LiveZoneOutcome::NoChange { manifest } => {
                assert!(manifest
                    .block_outcomes
                    .iter()
                    .all(|b| matches!(b.action, BlockAction::BelowByteThreshold { .. })));
            }
            _ => panic!("expected NoChange"),
        }
    }

    #[test]
    fn user_array_text_parts_planned() {
        let b = body(json!({
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ]
            }]
        }));
        let out = compress_openai_chat_live_zone(&b, AuthMode::Payg, "gpt-4o").unwrap();
        match &out {
            LiveZoneOutcome::NoChange { manifest } => {
                assert_eq!(manifest.block_outcomes.len(), 1);
                assert_eq!(manifest.block_outcomes[0].block_type, "user_text");
            }
            _ => panic!("expected NoChange"),
        }
    }

    #[test]
    fn picks_latest_tool_only() {
        let b = body(json!({
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "tool", "tool_call_id": "t1", "content": "early"},
                {"role": "user", "content": "again"},
                {"role": "tool", "tool_call_id": "t2", "content": "late"},
            ]
        }));
        let out = compress_openai_chat_live_zone(&b, AuthMode::Payg, "gpt-4o").unwrap();
        let manifest = match &out {
            LiveZoneOutcome::NoChange { manifest } => manifest,
            LiveZoneOutcome::Modified { manifest, .. } => manifest,
        };
        let tool_block = manifest
            .block_outcomes
            .iter()
            .find(|b| b.block_type == "tool_content")
            .expect("tool block recorded");
        assert_eq!(tool_block.message_index, 3);
        let user_block = manifest
            .block_outcomes
            .iter()
            .find(|b| b.block_type == "user_string")
            .expect("user block recorded");
        assert_eq!(user_block.message_index, 2);
    }
}


const RESPONSES_OUTPUT_MIN_BYTES: usize = 512;

pub fn compress_openai_responses_live_zone(
    body_raw: &[u8],
    _auth_mode: AuthMode,
    model: &str,
) -> Result<LiveZoneOutcome, LiveZoneError> {
    let parsed: Value = serde_json::from_slice(body_raw).map_err(LiveZoneError::BodyNotJson)?;

    let items = parsed
        .get("input")
        .or_else(|| parsed.get("messages"))
        .and_then(Value::as_array)
        .ok_or(LiveZoneError::NoMessagesArray)?;

    if items.is_empty() {
        return Ok(LiveZoneOutcome::NoChange {
            manifest: CompressionManifest::empty(),
        });
    }

    let items_total = items.len();

    let mut headroom_retrieve_call_ids: HashSet<&str> = HashSet::new();
    for item in items {
        if item.get("type").and_then(Value::as_str) != Some("function_call") {
            continue;
        }
        let name = item.get("name").and_then(Value::as_str).unwrap_or("");
        if name == "headroom_retrieve" || name.ends_with("__headroom_retrieve") {
            if let Some(call_id) = item.get("call_id").and_then(Value::as_str) {
                headroom_retrieve_call_ids.insert(call_id);
            }
        }
    }

    let mut output_candidates: Vec<(usize, &str)> = Vec::new();
    let latest_message: Option<usize> = None;

    for (idx, item) in items.iter().enumerate() {
        let type_tag = item.get("type").and_then(Value::as_str).unwrap_or("");
        match type_tag {
            "function_call_output" | "local_shell_call_output" | "apply_patch_call_output" => {
                let call_id = item.get("call_id").and_then(Value::as_str);
                if call_id.is_some_and(|id| headroom_retrieve_call_ids.contains(id)) {
                    continue;
                }
                output_candidates.push((idx, type_tag));
            }
            _ => {}
        }
    }

    let mut candidates = output_candidates;
    if let Some(idx) = latest_message {
        candidates.push((idx, "message"));
    }

    if candidates.is_empty() {
        return Ok(LiveZoneOutcome::NoChange {
            manifest: CompressionManifest {
                messages_total: items_total,
                messages_below_frozen_floor: 0,
                latest_user_message_index: latest_message,
                block_outcomes: Vec::new(),
            },
        });
    }

    let mut all_slots: Vec<(usize, ResponsesPlanSlot)> = Vec::new();
    for (idx, kind_tag) in candidates {
        match plan_responses_item(body_raw, idx, kind_tag) {
            Ok(Some(slot)) => all_slots.push((idx, slot)),
            Ok(None) => {}
            Err(_) => {
                continue;
            }
        }
    }

    if all_slots.is_empty() {
        return Ok(LiveZoneOutcome::NoChange {
            manifest: CompressionManifest {
                messages_total: items_total,
                messages_below_frozen_floor: 0,
                latest_user_message_index: latest_message,
                block_outcomes: Vec::new(),
            },
        });
    }

    let tokenizer = get_tokenizer(model);
    let mut block_outcomes: Vec<BlockOutcome> = Vec::with_capacity(all_slots.len());
    let mut replacements: Vec<Replacement> = Vec::new();

    for (msg_idx, slot) in all_slots {
        if slot.is_output_item && slot.content_text.len() < RESPONSES_OUTPUT_MIN_BYTES {
            block_outcomes.push(BlockOutcome {
                message_index: msg_idx,
                block_index: slot.block_index,
                block_type: slot.block_type.clone(),
                action: BlockAction::BelowByteThreshold {
                    content_type: "output_item",
                    byte_count: slot.content_text.len(),
                    threshold_bytes: RESPONSES_OUTPUT_MIN_BYTES,
                },
            });
            continue;
        }
        let detected = detect_content_type(&slot.content_text);
        let outcome = compress_one_block(
            &slot.content_text,
            detected.content_type,
            slot.content_byte_range,
            msg_idx,
            slot.block_index,
            slot.block_type,
            tokenizer.as_ref(),
            &mut replacements,
            None,
        );
        block_outcomes.push(outcome);
    }

    let manifest = CompressionManifest {
        messages_total: items_total,
        messages_below_frozen_floor: 0,
        latest_user_message_index: latest_message,
        block_outcomes,
    };

    if !manifest.has_compressed_block() || replacements.is_empty() {
        return Ok(LiveZoneOutcome::NoChange { manifest });
    }

    let new_bytes = apply_replacements(body_raw, &mut replacements);
    let new_body_str = match std::str::from_utf8(&new_bytes) {
        Ok(s) => s,
        Err(_) => return Ok(LiveZoneOutcome::NoChange { manifest }),
    };
    let raw = match RawValue::from_string(new_body_str.to_string()) {
        Ok(r) => r,
        Err(_) => return Ok(LiveZoneOutcome::NoChange { manifest }),
    };

    Ok(LiveZoneOutcome::Modified {
        new_body: raw,
        manifest,
    })
}

struct ResponsesPlanSlot {
    block_index: Option<usize>,
    block_type: String,
    content_text: String,
    content_byte_range: (usize, usize),
    is_output_item: bool,
}

#[derive(Deserialize)]
struct ResponsesBodyView<'a> {
    #[serde(borrow, default)]
    input: Option<Vec<&'a RawValue>>,
    #[serde(borrow, default)]
    messages: Option<Vec<&'a RawValue>>,
}

impl<'a> ResponsesBodyView<'a> {
    fn items(&self) -> Option<&Vec<&'a RawValue>> {
        self.input.as_ref().or(self.messages.as_ref())
    }
}

#[derive(Deserialize)]
struct OutputItemView<'a> {
    #[serde(borrow, default)]
    output: Option<&'a RawValue>,
}

#[derive(Deserialize)]
struct MessageItemView<'a> {
    #[serde(borrow, default)]
    content: Option<&'a RawValue>,
}

fn plan_responses_item(
    body_raw: &[u8],
    item_idx: usize,
    kind_tag: &str,
) -> Result<Option<ResponsesPlanSlot>, PlanError> {
    let body_str = std::str::from_utf8(body_raw).map_err(|_| PlanError::ParseFailed)?;
    let body: ResponsesBodyView<'_> =
        serde_json::from_str(body_str).map_err(|_| PlanError::ParseFailed)?;
    let items = body.items().ok_or(PlanError::ParseFailed)?;
    let item_raw = items.get(item_idx).ok_or(PlanError::TargetOutOfBounds)?;
    let item_offset_in_body =
        bytes_offset_of(body_str, item_raw.get()).ok_or(PlanError::OffsetMissing)?;

    match kind_tag {
        "function_call_output" | "local_shell_call_output" | "apply_patch_call_output" => {
            let view: OutputItemView<'_> =
                serde_json::from_str(item_raw.get()).map_err(|_| PlanError::ParseFailed)?;
            let Some(output_raw) = view.output else {
                return Ok(None);
            };
            let output_offset_in_item = bytes_offset_of(item_raw.get(), output_raw.get())
                .ok_or(PlanError::OffsetMissing)?;
            let output_offset_in_body = item_offset_in_body + output_offset_in_item;
            let output_str = output_raw.get();
            if !output_str.starts_with('"') {
                return Ok(None);
            }
            let unescaped: String =
                serde_json::from_str(output_str).map_err(|_| PlanError::ParseFailed)?;
            Ok(Some(ResponsesPlanSlot {
                block_index: None,
                block_type: kind_tag.to_string(),
                content_text: unescaped,
                content_byte_range: (
                    output_offset_in_body,
                    output_offset_in_body + output_str.len(),
                ),
                is_output_item: true,
            }))
        }
        "message" => {
            let view: MessageItemView<'_> =
                serde_json::from_str(item_raw.get()).map_err(|_| PlanError::ParseFailed)?;
            let Some(content_raw) = view.content else {
                return Ok(None);
            };
            let content_offset_in_item = bytes_offset_of(item_raw.get(), content_raw.get())
                .ok_or(PlanError::OffsetMissing)?;
            let content_offset_in_body = item_offset_in_body + content_offset_in_item;
            let content_str = content_raw.get();

            if content_str.starts_with('"') {
                let unescaped: String =
                    serde_json::from_str(content_str).map_err(|_| PlanError::ParseFailed)?;
                return Ok(Some(ResponsesPlanSlot {
                    block_index: None,
                    block_type: "message_string".to_string(),
                    content_text: unescaped,
                    content_byte_range: (
                        content_offset_in_body,
                        content_offset_in_body + content_str.len(),
                    ),
                    is_output_item: false,
                }));
            }

            let parts: Vec<&RawValue> =
                serde_json::from_str(content_str).map_err(|_| PlanError::ParseFailed)?;

            for (part_idx, part_raw) in parts.iter().enumerate() {
                let header: BlockHeader<'_> =
                    serde_json::from_str(part_raw.get()).map_err(|_| PlanError::ParseFailed)?;
                let block_type = header.r#type.unwrap_or("unknown");
                let is_text = block_type == "input_text"
                    || block_type == "output_text"
                    || block_type == "text";
                if !is_text {
                    continue;
                }

                #[derive(Deserialize)]
                struct TextHeader<'a> {
                    #[serde(borrow, default)]
                    text: Option<&'a RawValue>,
                }
                let h: TextHeader<'_> =
                    serde_json::from_str(part_raw.get()).map_err(|_| PlanError::ParseFailed)?;
                let Some(text_raw) = h.text else { continue };

                let part_offset_in_content =
                    bytes_offset_of(content_str, part_raw.get()).ok_or(PlanError::OffsetMissing)?;
                let part_offset_in_body = content_offset_in_body + part_offset_in_content;
                let text_offset_in_part = bytes_offset_of(part_raw.get(), text_raw.get())
                    .ok_or(PlanError::OffsetMissing)?;

                let text_str = text_raw.get();
                if !text_str.starts_with('"') {
                    continue;
                }
                let unescaped: String =
                    serde_json::from_str(text_str).map_err(|_| PlanError::ParseFailed)?;

                let text_start_in_body = part_offset_in_body + text_offset_in_part;
                let text_end_in_body = text_start_in_body + text_str.len();

                return Ok(Some(ResponsesPlanSlot {
                    block_index: Some(part_idx),
                    block_type: format!("message_{block_type}"),
                    content_text: unescaped,
                    content_byte_range: (text_start_in_body, text_end_in_body),
                    is_output_item: false,
                }));
            }
            Ok(None)
        }
        _ => Ok(None),
    }
}

#[cfg(test)]
mod openai_responses_tests {
    use super::*;
    use serde_json::json;

    fn body(value: Value) -> Vec<u8> {
        serde_json::to_vec(&value).unwrap()
    }

    #[test]
    fn empty_input_yields_no_change() {
        let b = body(json!({"model": "gpt-4o", "input": []}));
        let out = compress_openai_responses_live_zone(&b, AuthMode::Payg, DEFAULT_MODEL).unwrap();
        assert!(matches!(out, LiveZoneOutcome::NoChange { .. }));
    }

    #[test]
    fn no_input_field_errors() {
        let b = body(json!({"model": "gpt-4o"}));
        let err =
            compress_openai_responses_live_zone(&b, AuthMode::Payg, DEFAULT_MODEL).unwrap_err();
        assert!(matches!(err, LiveZoneError::NoMessagesArray));
    }

    #[test]
    fn invalid_json_errors() {
        let err = compress_openai_responses_live_zone(b"not json", AuthMode::Payg, DEFAULT_MODEL)
            .unwrap_err();
        assert!(matches!(err, LiveZoneError::BodyNotJson(_)));
    }

    #[test]
    fn output_below_512b_skipped() {
        let small = "x".repeat(256);
        let b = body(json!({
            "model": "gpt-4o",
            "input": [
                {"type": "function_call_output", "call_id": "c1", "output": small}
            ]
        }));
        let out = compress_openai_responses_live_zone(&b, AuthMode::Payg, "gpt-4o").unwrap();
        match &out {
            LiveZoneOutcome::NoChange { manifest } => {
                assert_eq!(manifest.block_outcomes.len(), 1);
                match &manifest.block_outcomes[0].action {
                    BlockAction::BelowByteThreshold {
                        content_type,
                        byte_count,
                        threshold_bytes,
                    } => {
                        assert_eq!(*content_type, "output_item");
                        assert_eq!(*byte_count, 256);
                        assert_eq!(*threshold_bytes, RESPONSES_OUTPUT_MIN_BYTES);
                    }
                    other => panic!("expected BelowByteThreshold, got {other:?}"),
                }
            }
            _ => panic!("expected NoChange"),
        }
    }

    #[test]
    fn plans_all_same_frame_function_outputs() {
        let b = body(json!({
            "model": "gpt-4o",
            "input": [
                {"type": "function_call_output", "call_id": "c1", "output": "early"},
                {"type": "function_call", "call_id": "c2", "name": "f", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "c2", "output": "late"},
            ]
        }));
        let out = compress_openai_responses_live_zone(&b, AuthMode::Payg, "gpt-4o").unwrap();
        let manifest = match &out {
            LiveZoneOutcome::NoChange { manifest } => manifest,
            LiveZoneOutcome::Modified { manifest, .. } => manifest,
        };
        let outputs: Vec<_> = manifest
            .block_outcomes
            .iter()
            .filter(|b| b.block_type == "function_call_output")
            .collect();
        assert_eq!(outputs.len(), 2);
        assert_eq!(outputs[0].message_index, 0);
        assert_eq!(outputs[1].message_index, 2);
    }

    #[test]
    fn compresses_multiple_same_frame_outputs() {
        let mut first = String::new();
        let mut second = String::new();
        for i in 0..400 {
            first.push_str(&format!(
                "./src/foo_{i}.rs:12: error[E0308]: mismatched types in module foo_{i}\n"
            ));
            second.push_str(&format!(
                "./tests/bar_{i}.rs:44: warning: unused variable in test bar_{i}\n"
            ));
        }
        let b = body(json!({
            "model": "gpt-4o",
            "input": [
                {"type": "function_call_output", "call_id": "c1", "output": first},
                {"type": "function_call", "call_id": "c2", "name": "f", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "c2", "output": second},
            ]
        }));
        let out = compress_openai_responses_live_zone(&b, AuthMode::Payg, "gpt-4o").unwrap();
        let manifest = match &out {
            LiveZoneOutcome::NoChange { manifest } => manifest,
            LiveZoneOutcome::Modified { manifest, .. } => manifest,
        };
        let compressed_outputs = manifest
            .block_outcomes
            .iter()
            .filter(|b| {
                b.block_type == "function_call_output"
                    && matches!(b.action, BlockAction::Compressed { .. })
            })
            .count();
        assert_eq!(compressed_outputs, 2, "{manifest:?}");
    }

    #[test]
    fn unknown_item_types_passthrough_no_slot() {
        let b = body(json!({
            "model": "gpt-4o",
            "input": [
                {"type": "reasoning", "id": "r1", "encrypted_content": "opaque"},
                {"type": "compaction", "id": "k1", "encrypted_content": "opaque"},
                {"type": "future_item_v2", "novel": true},
            ]
        }));
        let out = compress_openai_responses_live_zone(&b, AuthMode::Payg, "gpt-4o").unwrap();
        match &out {
            LiveZoneOutcome::NoChange { manifest } => {
                assert!(manifest.block_outcomes.is_empty());
            }
            _ => panic!("expected NoChange"),
        }
    }

    #[test]
    fn large_log_output_compressed() {
        let mut log = String::new();
        for i in 0..400 {
            log.push_str(&format!(
                "[2024-01-01 00:00:00] INFO compile.rs:42 building module foo_{i}\n"
            ));
        }
        assert!(log.len() > 2048);
        let b = body(json!({
            "model": "gpt-4o",
            "input": [
                {"type": "local_shell_call_output", "call_id": "c1", "output": log}
            ]
        }));
        let out = compress_openai_responses_live_zone(&b, AuthMode::Payg, "gpt-4o").unwrap();
        match &out {
            LiveZoneOutcome::Modified { new_body, manifest } => {
                let new = new_body.get();
                assert!(new.len() < b.len());
                assert!(manifest
                    .block_outcomes
                    .iter()
                    .any(|b| matches!(b.action, BlockAction::Compressed { .. })));
            }
            LiveZoneOutcome::NoChange { manifest } => {
                let attempted = manifest.block_outcomes.iter().any(|b| {
                    matches!(
                        b.action,
                        BlockAction::Compressed { .. } | BlockAction::RejectedNotSmaller { .. }
                    )
                });
                assert!(
                    attempted,
                    "expected dispatcher to attempt compression on a 2KB+ log fixture: {manifest:?}"
                );
            }
        }
    }

    #[test]
    fn message_user_content_not_in_live_zone() {
        let b = body(json!({
            "model": "gpt-4o",
            "input": [
                {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": "describe this"}]}
            ]
        }));
        let out = compress_openai_responses_live_zone(&b, AuthMode::Payg, "gpt-4o").unwrap();
        match &out {
            LiveZoneOutcome::NoChange { manifest } => {
                assert!(manifest.block_outcomes.is_empty());
            }
            _ => panic!("expected NoChange"),
        }
    }

    #[test]
    fn headroom_retrieve_output_not_in_live_zone() {
        let retrieved = "retrieved original content ".repeat(100);
        let b = body(json!({
            "model": "gpt-4o",
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_retrieve",
                    "name": "mcp__headroom__headroom_retrieve",
                    "arguments": "{}"
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_retrieve",
                    "output": retrieved
                }
            ]
        }));
        let out = compress_openai_responses_live_zone(&b, AuthMode::Payg, "gpt-4o").unwrap();
        match &out {
            LiveZoneOutcome::NoChange { manifest } => {
                assert!(manifest.block_outcomes.is_empty());
            }
            _ => panic!("expected NoChange"),
        }
    }

    #[test]
    fn assistant_message_not_in_live_zone() {
        let b = body(json!({
            "model": "gpt-4o",
            "input": [
                {"type": "message", "role": "assistant",
                 "content": [{"type": "output_text", "text": "answer"}]}
            ]
        }));
        let out = compress_openai_responses_live_zone(&b, AuthMode::Payg, "gpt-4o").unwrap();
        match &out {
            LiveZoneOutcome::NoChange { manifest } => {
                assert!(manifest.block_outcomes.is_empty());
            }
            _ => panic!("expected NoChange"),
        }
    }

    #[test]
    fn no_change_reason_empty_input_is_no_eligible_items() {
        let manifest = CompressionManifest::empty();
        assert_eq!(
            summarize_openai_responses_no_change_reason(&manifest),
            "no_eligible_items"
        );
    }

    #[test]
    fn no_change_reason_prefers_output_floor() {
        let manifest = CompressionManifest {
            messages_total: 1,
            messages_below_frozen_floor: 0,
            latest_user_message_index: Some(0),
            block_outcomes: vec![BlockOutcome {
                message_index: 0,
                block_index: None,
                block_type: "function_call_output".to_string(),
                action: BlockAction::BelowByteThreshold {
                    content_type: "output_item",
                    byte_count: 1024,
                    threshold_bytes: RESPONSES_OUTPUT_MIN_BYTES,
                },
            }],
        };
        assert_eq!(
            summarize_openai_responses_no_change_reason(&manifest),
            "below_output_floor"
        );
    }
}
