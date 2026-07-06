#![allow(clippy::collapsible_if)]

pub mod auth_mode;
pub mod cache_control;
pub mod ccr;
pub mod compression_policy;
mod onnx_cpu;
pub mod relevance;
pub mod signals;
pub mod tokenizer;
pub mod transforms;

use std::sync::OnceLock;

pub use cache_control::compute_frozen_count;

use transforms::{
    ContentType,
    diff_compressor::{DiffCompressor, DiffCompressorConfig},
    log_compressor::{LogCompressor, LogCompressorConfig},
    search_compressor::{SearchCompressor, SearchCompressorConfig},
    smart_crusher::{SmartCrusher, SmartCrusherConfig},
    text_crusher::{TextCrusher, TextCrusherConfig},
};

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

pub fn route_and_compress(content: &str) -> String {
    if content.len() < 512 {
        return content.to_string();
    }

    let detection = transforms::detect_content_type(content);
    let content_type = detection.content_type;

    match content_type {
        ContentType::JsonArray => {
            let result = smart_crusher().crush(content, "", 0.0);
            if result.was_modified { result.compressed } else { content.to_string() }
        }
        ContentType::BuildOutput => {
            let (result, _) = log_compressor().compress(content, 0.0);
            if result.compressed != result.original { result.compressed } else { content.to_string() }
        }
        ContentType::SearchResults => {
            let (result, _) = search_compressor().compress(content, "", 0.0);
            if result.compressed != result.original { result.compressed } else { content.to_string() }
        }
        ContentType::GitDiff => {
            let result = diff_compressor().compress(content, "");
            if result.compressed != content { result.compressed } else { content.to_string() }
        }
        ContentType::PlainText | ContentType::SourceCode => {
            let result = text_crusher().compress(content, "", None);
            if result.compressed != content { result.compressed } else { content.to_string() }
        }
        ContentType::Html => content.to_string(),
    }
}
