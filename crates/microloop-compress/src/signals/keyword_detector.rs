
use std::collections::BTreeMap;

use aho_corasick::{AhoCorasick, AhoCorasickBuilder, MatchKind};

use super::line_importance::{
    ImportanceCategory, ImportanceContext, ImportanceSignal, LineImportanceDetector,
};

const KEYWORD_CONFIDENCE: f32 = 0.7;

const ERROR_PRIORITY: f32 = 0.95;
const WARNING_PRIORITY: f32 = 0.75;
const SECURITY_PRIORITY: f32 = 0.85;
const IMPORTANCE_PRIORITY: f32 = 0.6;
const MARKDOWN_PRIORITY: f32 = 0.45;

#[derive(Debug, Clone)]
pub struct KeywordRegistry {
    pub error: Vec<&'static str>,
    pub warning: Vec<&'static str>,
    pub importance: Vec<&'static str>,
    pub security: Vec<&'static str>,
    pub markdown_prefixes: Vec<&'static str>,
    pub error_indicators: Vec<&'static str>,
}

impl KeywordRegistry {
    pub fn default_set() -> Self {
        Self {
            error: vec![
                "error",
                "exception",
                "fail",
                "failed",
                "failure",
                "fatal",
                "critical",
                "crash",
                "panic",
                "abort",
                "timeout",
                "denied",
                "rejected",
            ],
            warning: vec!["warn", "warning"],
            importance: vec![
                "important",
                "note",
                "todo",
                "fixme",
                "hack",
                "xxx",
                "bug",
                "fix",
            ],
            security: vec!["security", "auth", "password", "secret"],
            markdown_prefixes: vec!["# ", "## ", "### ", "#### ", "**", "> "],
            error_indicators: vec![
                "error",
                "fail",
                "exception",
                "traceback",
                "fatal",
                "panic",
                "crash",
            ],
        }
    }

    pub fn as_map(&self) -> BTreeMap<&'static str, Vec<&'static str>> {
        let mut m = BTreeMap::new();
        m.insert("error", self.error.clone());
        m.insert("warning", self.warning.clone());
        m.insert("importance", self.importance.clone());
        m.insert("security", self.security.clone());
        m.insert("markdown_prefixes", self.markdown_prefixes.clone());
        m.insert("error_indicators", self.error_indicators.clone());
        m
    }
}

struct CategoryAutomaton {
    automaton: AhoCorasick,
    categories: Vec<ImportanceCategory>,
}

impl CategoryAutomaton {
    fn build(entries: &[(ImportanceCategory, &[&'static str])]) -> Self {
        let mut patterns = Vec::new();
        let mut categories = Vec::new();
        for (cat, words) in entries {
            for w in *words {
                patterns.push(*w);
                categories.push(*cat);
            }
        }
        let automaton = AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .match_kind(MatchKind::LeftmostLongest)
            .build(&patterns)
            .expect("keyword automaton must build (static input)");
        Self {
            automaton,
            categories,
        }
    }

    fn first_word_match(&self, line: &str) -> Option<ImportanceCategory> {
        let bytes = line.as_bytes();
        for m in self.automaton.find_iter(line) {
            if is_word_boundary(bytes, m.start(), m.end()) {
                return Some(self.categories[m.pattern().as_usize()]);
            }
        }
        None
    }
}

pub struct KeywordDetector {
    registry: KeywordRegistry,
    universal: CategoryAutomaton,
    warning: CategoryAutomaton,
    security: CategoryAutomaton,
    indicators: AhoCorasick,
}

impl KeywordDetector {
    pub fn new() -> Self {
        Self::with_registry(KeywordRegistry::default_set())
    }

    pub fn with_registry(registry: KeywordRegistry) -> Self {
        let universal = CategoryAutomaton::build(&[
            (ImportanceCategory::Error, &registry.error),
            (ImportanceCategory::Importance, &registry.importance),
        ]);
        let warning = CategoryAutomaton::build(&[(ImportanceCategory::Warning, &registry.warning)]);
        let security =
            CategoryAutomaton::build(&[(ImportanceCategory::Security, &registry.security)]);
        let indicators = AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .match_kind(MatchKind::LeftmostLongest)
            .build(&registry.error_indicators)
            .expect("indicator automaton must build (static input)");
        Self {
            registry,
            universal,
            warning,
            security,
            indicators,
        }
    }

    pub fn contains_error_indicator(&self, text: &str) -> bool {
        self.indicators.is_match(text)
    }

    pub fn registry(&self) -> &KeywordRegistry {
        &self.registry
    }

    fn match_in_context(
        &self,
        line: &str,
        ctx: ImportanceContext,
    ) -> Option<(ImportanceCategory, f32)> {
        if let Some(cat) = self.universal.first_word_match(line) {
            let priority = priority_for(cat);
            return Some((cat, priority));
        }
        match ctx {
            ImportanceContext::Diff => {
                if let Some(cat) = self.security.first_word_match(line) {
                    return Some((cat, priority_for(cat)));
                }
            }
            ImportanceContext::Text | ImportanceContext::Search | ImportanceContext::Log => {
                if let Some(cat) = self.warning.first_word_match(line) {
                    return Some((cat, priority_for(cat)));
                }
            }
        }
        if matches!(ctx, ImportanceContext::Text) {
            if let Some(prefix) = self
                .registry
                .markdown_prefixes
                .iter()
                .find(|p| line.starts_with(*p))
            {
                let _ = prefix;
                return Some((ImportanceCategory::Markdown, MARKDOWN_PRIORITY));
            }
        }
        None
    }
}

impl Default for KeywordDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl LineImportanceDetector for KeywordDetector {
    fn score(&self, line: &str, ctx: ImportanceContext) -> ImportanceSignal {
        match self.match_in_context(line, ctx) {
            Some((category, priority)) => {
                ImportanceSignal::matched(category, priority, KEYWORD_CONFIDENCE)
            }
            None => ImportanceSignal::neutral(),
        }
    }
}

const fn priority_for(category: ImportanceCategory) -> f32 {
    match category {
        ImportanceCategory::Error => ERROR_PRIORITY,
        ImportanceCategory::Warning => WARNING_PRIORITY,
        ImportanceCategory::Security => SECURITY_PRIORITY,
        ImportanceCategory::Importance => IMPORTANCE_PRIORITY,
        ImportanceCategory::Markdown => MARKDOWN_PRIORITY,
    }
}

fn is_word_boundary(bytes: &[u8], start: usize, end: usize) -> bool {
    let left_ok = start == 0 || !is_word_byte(bytes[start - 1]);
    let right_ok = end == bytes.len() || !is_word_byte(bytes[end]);
    left_ok && right_ok
}

#[inline]
fn is_word_byte(b: u8) -> bool {
    matches!(b, b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_')
}

#[cfg(test)]
mod tests {
    use super::*;

    fn detect(line: &str, ctx: ImportanceContext) -> ImportanceSignal {
        KeywordDetector::new().score(line, ctx)
    }

    #[test]
    fn fires_on_uppercase_error_in_search() {
        let s = detect("ERROR: connection refused", ImportanceContext::Search);
        assert_eq!(s.category, Some(ImportanceCategory::Error));
        assert!(s.priority > 0.9);
    }

    #[test]
    fn timeout_now_classified_as_error_in_diff() {
        let s = detect(
            "FATAL: timeout connecting upstream",
            ImportanceContext::Diff,
        );
        assert_eq!(s.category, Some(ImportanceCategory::Error));
    }

    #[test]
    fn rejected_now_classified_as_error() {
        let s = detect("auth request rejected", ImportanceContext::Diff);
        assert_eq!(s.category, Some(ImportanceCategory::Error));
    }

    #[test]
    fn token_no_longer_flags_security_in_llm_proxy_context() {
        let s = detect(
            "input_tokens=512 output_tokens=256",
            ImportanceContext::Diff,
        );
        assert!(!s.is_match());
    }

    #[test]
    fn auth_still_flags_security_in_diff() {
        let s = detect("missing auth header", ImportanceContext::Diff);
        assert_eq!(s.category, Some(ImportanceCategory::Security));
    }

    #[test]
    fn warning_fires_in_search_but_not_diff() {
        let in_search = detect("warning: deprecated API", ImportanceContext::Search);
        assert_eq!(in_search.category, Some(ImportanceCategory::Warning));

        let in_diff = detect(
            "warning: deprecated API alone with no errors",
            ImportanceContext::Diff,
        );
        assert_ne!(in_diff.category, Some(ImportanceCategory::Warning));
    }

    #[test]
    fn markdown_header_fires_only_in_text() {
        let in_text = detect("# Important section", ImportanceContext::Text);
        let _ = in_text;
        let prefix_only = detect("# Section", ImportanceContext::Text);
        assert_eq!(prefix_only.category, Some(ImportanceCategory::Markdown));
        let same_line_in_diff = detect("# Section", ImportanceContext::Diff);
        assert!(!same_line_in_diff.is_match());
    }

    #[test]
    fn word_boundary_excludes_substring_matches() {
        let s = detect("the panicker showed up late", ImportanceContext::Search);
        assert!(!s.is_match());
    }

    #[test]
    fn neutral_line_returns_zero_confidence() {
        let s = detect("the quick brown fox", ImportanceContext::Text);
        assert!(!s.is_match());
        assert_eq!(s.confidence, 0.0);
    }

    #[test]
    fn contains_error_indicator_is_lax_substring_match() {
        let det = KeywordDetector::new();
        assert!(det.contains_error_indicator("the request errored out"));
        assert!(det.contains_error_indicator("traceback follows"));
        assert!(!det.contains_error_indicator("everything is fine"));
    }

    #[test]
    fn registry_snapshot_has_token_dropped() {
        let reg = KeywordRegistry::default_set();
        assert!(!reg.security.contains(&"token"));
        assert!(reg.security.contains(&"auth"));
        assert!(reg.error.contains(&"timeout"));
        assert!(reg.error.contains(&"abort"));
    }
}
