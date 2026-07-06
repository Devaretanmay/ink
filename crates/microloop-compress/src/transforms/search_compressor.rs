
use std::collections::{BTreeMap, BTreeSet};

use md5::{Digest, Md5};

use crate::ccr::CcrStore;
use crate::signals::{ImportanceContext, LineImportanceDetector};
use crate::transforms::adaptive_sizer::compute_optimal_k;


#[derive(Debug, Clone, PartialEq)]
pub struct SearchMatch {
    pub file: String,
    pub line_number: u64,
    pub content: String,
    pub score: f32,
}

impl SearchMatch {
    pub fn new(file: impl Into<String>, line_number: u64, content: impl Into<String>) -> Self {
        Self {
            file: file.into(),
            line_number,
            content: content.into(),
            score: 0.0,
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct FileMatches {
    pub file: String,
    pub matches: Vec<SearchMatch>,
}

impl FileMatches {
    pub fn new(file: impl Into<String>) -> Self {
        Self {
            file: file.into(),
            matches: Vec::new(),
        }
    }

    pub fn first(&self) -> Option<&SearchMatch> {
        self.matches.first()
    }

    pub fn last(&self) -> Option<&SearchMatch> {
        self.matches.last()
    }

    pub fn total_score(&self) -> f32 {
        self.matches.iter().map(|m| m.score).sum()
    }
}

#[derive(Debug, Clone)]
pub struct SearchCompressorConfig {
    pub max_matches_per_file: usize,
    pub always_keep_first: bool,
    pub always_keep_last: bool,
    pub max_total_matches: usize,
    pub max_files: usize,
    pub context_keywords: Vec<String>,
    pub boost_errors: bool,
    pub enable_ccr: bool,
    pub min_matches_for_ccr: usize,
    pub min_compression_ratio_for_ccr: f64,
    pub group_by_file: bool,
}

impl Default for SearchCompressorConfig {
    fn default() -> Self {
        Self {
            max_matches_per_file: 5,
            always_keep_first: true,
            always_keep_last: true,
            max_total_matches: 30,
            max_files: 15,
            context_keywords: Vec::new(),
            boost_errors: true,
            enable_ccr: true,
            min_matches_for_ccr: 10,
            min_compression_ratio_for_ccr: 0.8,
            group_by_file: false,
        }
    }
}

#[derive(Debug, Clone)]
pub struct SearchCompressionResult {
    pub compressed: String,
    pub original: String,
    pub original_match_count: usize,
    pub compressed_match_count: usize,
    pub files_affected: usize,
    pub compression_ratio: f64,
    pub cache_key: Option<String>,
    pub summaries: BTreeMap<String, String>,
}

impl SearchCompressionResult {
    pub fn tokens_saved_estimate(&self) -> i64 {
        let chars_saved = self.original.len() as i64 - self.compressed.len() as i64;
        chars_saved.max(0) / 4
    }

    pub fn matches_omitted(&self) -> usize {
        self.original_match_count
            .saturating_sub(self.compressed_match_count)
    }
}

#[derive(Debug, Clone, Default)]
pub struct SearchCompressorStats {
    pub lines_scanned: usize,
    pub lines_unparsed: usize,
    pub files_dropped: usize,
    pub matches_dropped_by_per_file_cap: usize,
    pub matches_dropped_by_global_cap: usize,
    pub ccr_emitted: bool,
    pub ccr_skip_reason: Option<&'static str>,
}


pub struct SearchCompressor {
    config: SearchCompressorConfig,
    importance: Box<dyn LineImportanceDetector>,
}

impl SearchCompressor {
    pub fn new(config: SearchCompressorConfig) -> Self {
        Self {
            config,
            importance: Box::new(crate::signals::KeywordDetector::new()),
        }
    }

    pub fn with_detector<D: LineImportanceDetector + 'static>(
        config: SearchCompressorConfig,
        detector: D,
    ) -> Self {
        Self {
            config,
            importance: Box::new(detector),
        }
    }

    pub fn config(&self) -> &SearchCompressorConfig {
        &self.config
    }

    pub fn compress(
        &self,
        content: &str,
        context: &str,
        bias: f64,
    ) -> (SearchCompressionResult, SearchCompressorStats) {
        self.compress_with_store(content, context, bias, None)
    }

    pub fn compress_with_store(
        &self,
        content: &str,
        context: &str,
        bias: f64,
        store: Option<&dyn CcrStore>,
    ) -> (SearchCompressionResult, SearchCompressorStats) {
        let mut stats = SearchCompressorStats::default();
        let parsed = self.parse_search_results(content, &mut stats);

        if parsed.is_empty() {
            return (
                SearchCompressionResult {
                    compressed: content.to_string(),
                    original: content.to_string(),
                    original_match_count: 0,
                    compressed_match_count: 0,
                    files_affected: 0,
                    compression_ratio: 1.0,
                    cache_key: None,
                    summaries: BTreeMap::new(),
                },
                stats,
            );
        }

        let original_count: usize = parsed.values().map(|fm| fm.matches.len()).sum();

        let mut scored = parsed;
        self.score_matches(&mut scored, context);

        let selected = self.select_matches(&scored, bias, &mut stats);

        let (compressed_body, summaries) = self.format_output(&selected, &scored);
        let compressed_count: usize = selected.values().map(|fm| fm.matches.len()).sum();
        let ratio = compressed_body.len() as f64 / content.len().max(1) as f64;

        let mut compressed = compressed_body;
        let mut cache_key = None;
        if self.config.enable_ccr {
            if original_count < self.config.min_matches_for_ccr {
                stats.ccr_skip_reason = Some("below min_matches_for_ccr");
            } else if ratio >= self.config.min_compression_ratio_for_ccr {
                stats.ccr_skip_reason = Some("compression ratio too high");
            } else if let Some(store) = store {
                let key = md5_hex_24(content);
                store.put(&key, content);
                let marker = format!(
                    "\n[{} matches compressed to {}. Retrieve more: hash={}]",
                    original_count, compressed_count, key
                );
                compressed.push_str(&marker);
                cache_key = Some(key);
                stats.ccr_emitted = true;
            } else {
                stats.ccr_skip_reason = Some("no store provided");
            }
        } else {
            stats.ccr_skip_reason = Some("ccr disabled in config");
        }

        let result = SearchCompressionResult {
            compressed,
            original: content.to_string(),
            original_match_count: original_count,
            compressed_match_count: compressed_count,
            files_affected: scored.len(),
            compression_ratio: ratio,
            cache_key,
            summaries,
        };
        (result, stats)
    }


    pub fn parse_search_results(
        &self,
        content: &str,
        stats: &mut SearchCompressorStats,
    ) -> BTreeMap<String, FileMatches> {
        let mut out: BTreeMap<String, FileMatches> = BTreeMap::new();
        for raw in content.split('\n') {
            let line = raw.trim();
            if line.is_empty() {
                continue;
            }
            stats.lines_scanned += 1;
            match parse_match_line(line) {
                Some((file, line_no, body)) => {
                    out.entry(file.to_string())
                        .or_insert_with(|| FileMatches::new(file))
                        .matches
                        .push(SearchMatch::new(file, line_no, body));
                }
                None => stats.lines_unparsed += 1,
            }
        }
        out
    }

    pub fn score_matches(&self, files: &mut BTreeMap<String, FileMatches>, context: &str) {
        let context_lower = context.to_ascii_lowercase();
        let context_words: Vec<&str> = context_lower
            .split_whitespace()
            .filter(|w| w.len() > 2)
            .collect();

        for fm in files.values_mut() {
            for m in &mut fm.matches {
                let mut score: f32 = 0.0;
                let content_lower = m.content.to_ascii_lowercase();

                for w in &context_words {
                    if content_lower.contains(w) {
                        score += 0.3;
                    }
                }

                if self.config.boost_errors {
                    let signal = self.importance.score(&m.content, ImportanceContext::Search);
                    if let Some(category) = signal.category {
                        let bump = match category {
                            crate::signals::ImportanceCategory::Error => 0.5,
                            crate::signals::ImportanceCategory::Warning => 0.4,
                            crate::signals::ImportanceCategory::Importance => 0.3,
                            crate::signals::ImportanceCategory::Security
                            | crate::signals::ImportanceCategory::Markdown => 0.0,
                        };
                        score += bump;
                    }
                }

                for kw in &self.config.context_keywords {
                    if content_lower.contains(&kw.to_ascii_lowercase()) {
                        score += 0.4;
                    }
                }

                m.score = score.min(1.0);
            }
        }
    }

    pub fn select_matches(
        &self,
        files: &BTreeMap<String, FileMatches>,
        bias: f64,
        stats: &mut SearchCompressorStats,
    ) -> BTreeMap<String, FileMatches> {
        let mut by_score: Vec<(&String, &FileMatches)> = files.iter().collect();
        by_score.sort_by(|a, b| {
            b.1.total_score()
                .partial_cmp(&a.1.total_score())
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        if by_score.len() > self.config.max_files {
            stats.files_dropped += by_score.len() - self.config.max_files;
            by_score.truncate(self.config.max_files);
        }

        let all_match_strings: Vec<String> = by_score
            .iter()
            .flat_map(|(file, fm)| {
                fm.matches
                    .iter()
                    .map(move |m| format!("{}:{}:{}", file, m.line_number, m.content))
            })
            .collect();
        let all_refs: Vec<&str> = all_match_strings.iter().map(|s| s.as_str()).collect();
        let adaptive_total =
            compute_optimal_k(&all_refs, bias, 5, Some(self.config.max_total_matches));

        let mut selected: BTreeMap<String, FileMatches> = BTreeMap::new();
        let mut total_selected: usize = 0;

        for (file, fm) in by_score {
            if total_selected >= adaptive_total {
                stats.matches_dropped_by_global_cap += fm.matches.len();
                continue;
            }

            let mut sorted = fm.matches.clone();
            sorted.sort_by(|a, b| {
                b.score
                    .partial_cmp(&a.score)
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then_with(|| a.line_number.cmp(&b.line_number))
            });

            let mut file_selected: Vec<SearchMatch> = Vec::new();
            let mut seen: BTreeSet<(u64, u64)> = BTreeSet::new();

            let remaining_cap = self
                .config
                .max_matches_per_file
                .min(adaptive_total.saturating_sub(total_selected));

            let push_unique = |m: &SearchMatch,
                               file_selected: &mut Vec<SearchMatch>,
                               seen: &mut BTreeSet<(u64, u64)>| {
                let key = (m.line_number, hash_u64(&m.content));
                if seen.insert(key) {
                    file_selected.push(m.clone());
                    true
                } else {
                    false
                }
            };

            if self.config.always_keep_first {
                if let Some(first) = fm.first() {
                    if file_selected.len() < remaining_cap {
                        push_unique(first, &mut file_selected, &mut seen);
                    }
                }
            }

            if self.config.always_keep_last && fm.matches.len() > 1 {
                if let Some(last) = fm.last() {
                    if file_selected.len() < remaining_cap {
                        push_unique(last, &mut file_selected, &mut seen);
                    }
                }
            }

            for m in &sorted {
                if file_selected.len() >= remaining_cap {
                    break;
                }
                push_unique(m, &mut file_selected, &mut seen);
            }

            file_selected.sort_by_key(|m| m.line_number);

            let dropped_here = fm.matches.len().saturating_sub(file_selected.len());
            stats.matches_dropped_by_per_file_cap += dropped_here;

            total_selected += file_selected.len();
            selected.insert(
                file.clone(),
                FileMatches {
                    file: file.clone(),
                    matches: file_selected,
                },
            );
        }

        selected
    }

    pub fn format_output(
        &self,
        selected: &BTreeMap<String, FileMatches>,
        original: &BTreeMap<String, FileMatches>,
    ) -> (String, BTreeMap<String, String>) {
        let mut lines: Vec<String> = Vec::new();
        let mut summaries: BTreeMap<String, String> = BTreeMap::new();
        let grouped = self.config.group_by_file;

        for (file, fm) in selected {
            if grouped {
                if !lines.is_empty() {
                    lines.push(String::new());
                }
                lines.push(file.clone());
                for m in &fm.matches {
                    lines.push(format!("{}:{}", m.line_number, m.content));
                }
            } else {
                for m in &fm.matches {
                    lines.push(format!("{}:{}:{}", m.file, m.line_number, m.content));
                }
            }
            if let Some(orig_fm) = original.get(file) {
                if orig_fm.matches.len() > fm.matches.len() {
                    let omitted = orig_fm.matches.len() - fm.matches.len();
                    let summary = if grouped {
                        format!("[... and {} more matches]", omitted)
                    } else {
                        format!("[... and {} more matches in {}]", omitted, file)
                    };
                    lines.push(summary.clone());
                    summaries.insert(file.clone(), summary);
                }
            }
        }

        (lines.join("\n"), summaries)
    }
}


fn parse_match_line(line: &str) -> Option<(&str, u64, &str)> {
    let bytes = line.as_bytes();
    let scan_start = if bytes.len() >= 3
        && bytes[0].is_ascii_alphabetic()
        && bytes[1] == b':'
        && (bytes[2] == b'\\' || bytes[2] == b'/')
    {
        2
    } else {
        0
    };

    let mut i = scan_start;
    while i < bytes.len() {
        if bytes[i] == b':' || bytes[i] == b'-' {
            if i > 0 && (bytes[i - 1] == b':' || bytes[i - 1] == b'-') {
                i += 1;
                continue;
            }
            let digits_start = i + 1;
            let mut j = digits_start;
            while j < bytes.len() && bytes[j].is_ascii_digit() {
                j += 1;
            }
            if j > digits_start && j < bytes.len() && (bytes[j] == b':' || bytes[j] == b'-') {
                if i == 0 {
                    return None;
                }
                let file = &line[..i];
                let line_no = std::str::from_utf8(&bytes[digits_start..j])
                    .ok()
                    .and_then(|s| s.parse::<u64>().ok())?;
                let content = &line[j + 1..];
                return Some((file, line_no, content));
            }
        }
        i += 1;
    }
    None
}


fn hash_u64(s: &str) -> u64 {
    use std::hash::{Hash, Hasher};
    let mut h = std::collections::hash_map::DefaultHasher::new();
    s.hash(&mut h);
    h.finish()
}

fn md5_hex_24(s: &str) -> String {
    let mut hasher = Md5::new();
    hasher.update(s.as_bytes());
    let digest = hasher.finalize();
    let mut hex = String::with_capacity(32);
    for b in digest {
        hex.push_str(&format!("{:02x}", b));
    }
    hex.truncate(24);
    hex
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ccr::InMemoryCcrStore;

    fn parse_line(line: &str) -> Option<(String, u64, String)> {
        parse_match_line(line).map(|(f, n, c)| (f.to_string(), n, c.to_string()))
    }

    #[test]
    fn parses_standard_grep_line() {
        assert_eq!(
            parse_line("src/main.py:42:def main():"),
            Some(("src/main.py".into(), 42, "def main():".into()))
        );
    }

    #[test]
    fn parses_ripgrep_context_line() {
        assert_eq!(
            parse_line("src/main.py-43-context after match"),
            Some(("src/main.py".into(), 43, "context after match".into()))
        );
    }

    #[test]
    fn fixed_in_3e2_handles_windows_path_with_backslash() {
        assert_eq!(
            parse_line(r"C:\Users\foo\bar.py:42:def main():"),
            Some((r"C:\Users\foo\bar.py".into(), 42, "def main():".into()))
        );
    }

    #[test]
    fn fixed_in_3e2_handles_windows_path_with_forward_slash() {
        assert_eq!(
            parse_line("C:/Users/foo/bar.py:42:def main():"),
            Some(("C:/Users/foo/bar.py".into(), 42, "def main():".into()))
        );
    }

    #[test]
    fn fixed_in_3e2_handles_dashes_in_filename_with_ripgrep_context() {
        assert_eq!(
            parse_line("pre-commit-config.yaml-42-fail_fast: true"),
            Some((
                "pre-commit-config.yaml".into(),
                42,
                "fail_fast: true".into()
            ))
        );
    }

    #[test]
    fn preserves_colons_in_match_content() {
        let line = "config.py:10:DATABASE_URL = \"postgres://localhost/db\"";
        let result = parse_line(line);
        assert!(result.is_some());
        let (file, num, content) = result.unwrap();
        assert_eq!(file, "config.py");
        assert_eq!(num, 10);
        assert!(content.contains("postgres"));
    }

    #[test]
    fn rejects_lines_without_line_number_marker() {
        assert!(parse_line("just a normal line of prose").is_none());
        assert!(parse_line("file.py:not-a-number:something").is_none());
        assert!(parse_line(":42:something").is_none());
    }

    #[test]
    fn rejects_negative_line_numbers() {
        assert!(parse_line("src/file.py:-1:invalid").is_none());
        assert!(parse_line("src/file.py--1-invalid").is_none());
    }

    #[test]
    fn parser_groups_by_file_and_counts() {
        let compressor = SearchCompressor::new(SearchCompressorConfig::default());
        let content = "\
src/main.py:42:def main():
src/main.py:43:    pass
src/utils.py:15:def util():
just prose, no marker
src/main.py-44-context line";
        let mut stats = SearchCompressorStats::default();
        let parsed = compressor.parse_search_results(content, &mut stats);
        assert_eq!(parsed.len(), 2);
        assert_eq!(parsed["src/main.py"].matches.len(), 3);
        assert_eq!(parsed["src/utils.py"].matches.len(), 1);
        assert_eq!(stats.lines_unparsed, 1);
        assert_eq!(stats.lines_scanned, 5);
    }

    #[test]
    fn scoring_boosts_error_lines_in_search_context() {
        let compressor = SearchCompressor::new(SearchCompressorConfig {
            context_keywords: vec!["auth".into()],
            ..Default::default()
        });
        let mut files = BTreeMap::new();
        let mut fm = FileMatches::new("src/auth.py");
        fm.matches
            .push(SearchMatch::new("src/auth.py", 10, "ERROR auth failed"));
        fm.matches
            .push(SearchMatch::new("src/auth.py", 11, "plain auth line"));
        files.insert("src/auth.py".into(), fm);

        compressor.score_matches(&mut files, "find auth error");
        let scored = &files["src/auth.py"].matches;
        assert_eq!(scored[0].score, 1.0);
        assert!(scored[1].score > 0.0 && scored[1].score < 1.0);
    }

    #[test]
    fn select_respects_per_file_cap_and_global_cap() {
        let compressor = SearchCompressor::new(SearchCompressorConfig {
            max_matches_per_file: 2,
            max_total_matches: 6,
            max_files: 2,
            always_keep_first: true,
            always_keep_last: true,
            ..Default::default()
        });
        let mut files = BTreeMap::new();
        for (file, n) in [("a.py", 5), ("b.py", 4), ("c.py", 3)] {
            let mut fm = FileMatches::new(file);
            for i in 0..n {
                fm.matches
                    .push(SearchMatch::new(file, i + 1, format!("line {}", i + 1)));
            }
            files.insert(file.into(), fm);
        }

        let mut stats = SearchCompressorStats::default();
        let selected = compressor.select_matches(&files, 1.0, &mut stats);

        assert_eq!(selected.len(), 2);
        assert!(stats.files_dropped >= 1);
        for fm in selected.values() {
            assert!(fm.matches.len() <= 2);
            assert!(fm
                .matches
                .windows(2)
                .all(|w| w[0].line_number < w[1].line_number));
        }
    }

    #[test]
    fn empty_input_returns_unchanged() {
        let compressor = SearchCompressor::new(SearchCompressorConfig::default());
        let (result, _) = compressor.compress("plain text only", "", 1.0);
        assert_eq!(result.original_match_count, 0);
        assert_eq!(result.compressed, "plain text only");
        assert_eq!(result.compression_ratio, 1.0);
    }

    #[test]
    fn ccr_marker_emitted_when_thresholds_clear() {
        let compressor = SearchCompressor::new(SearchCompressorConfig {
            max_matches_per_file: 2,
            max_total_matches: 4,
            min_matches_for_ccr: 5,
            min_compression_ratio_for_ccr: 0.95,
            ..Default::default()
        });
        let mut content = String::new();
        for i in 1..=12 {
            content.push_str(&format!("src/main.py:{}:line content {}\n", i, i));
        }
        let store = InMemoryCcrStore::new();
        let (result, stats) = compressor.compress_with_store(&content, "", 1.0, Some(&store));
        assert!(result.cache_key.is_some());
        assert!(stats.ccr_emitted);
        assert!(result.compressed.contains("[12 matches compressed to"));
        let key = result.cache_key.as_ref().unwrap();
        assert_eq!(store.get(key).unwrap(), content);
    }

    #[test]
    fn ccr_skipped_when_below_min_matches() {
        let compressor = SearchCompressor::new(SearchCompressorConfig {
            min_matches_for_ccr: 100,
            ..Default::default()
        });
        let content = "src/main.py:1:hi\nsrc/main.py:2:bye\n";
        let store = InMemoryCcrStore::new();
        let (_, stats) = compressor.compress_with_store(content, "", 1.0, Some(&store));
        assert!(!stats.ccr_emitted);
        assert_eq!(stats.ccr_skip_reason, Some("below min_matches_for_ccr"));
        assert_eq!(store.len(), 0);
    }

    #[test]
    fn ccr_skipped_when_disabled() {
        let compressor = SearchCompressor::new(SearchCompressorConfig {
            enable_ccr: false,
            ..Default::default()
        });
        let mut content = String::new();
        for i in 1..=20 {
            content.push_str(&format!("src/main.py:{}:line\n", i));
        }
        let store = InMemoryCcrStore::new();
        let (_, stats) = compressor.compress_with_store(&content, "", 1.0, Some(&store));
        assert!(!stats.ccr_emitted);
        assert_eq!(stats.ccr_skip_reason, Some("ccr disabled in config"));
    }
}
