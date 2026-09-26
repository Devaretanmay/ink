//! Normalization of actions, observations and error signatures.
//!
//! Volatile tokens (ANSI escapes, UUIDs, git/docker hashes, hex addresses,
//! timestamps, temp paths, PIDs, ephemeral ports) are masked so that
//! structurally identical steps compare equal across runs.

use regex::Regex;
use std::sync::LazyLock;

static ANSI_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\x1b\[[0-9;]*[a-zA-Z]").unwrap());
static UUID_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
        .unwrap()
});
static HASH_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b[0-9a-fA-F]{40,64}\b").unwrap());
static HEX_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b0x[0-9a-fA-F]{4,16}\b").unwrap());
static TIMESTAMP_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b")
        .unwrap()
});
static TMP_PATH_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"/(?:tmp|var/folders/[^\s/]+/[^\s/]+/[^\s/]+|private/var/folders/[^\s/]+/[^\s/]+/[^\s/]+|root/\.cache/[^\s/]+)/[^\s\x22':;]+")
        .unwrap()
});
static PID_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\b(?:pid|PID|process|PROCESS)\s*[=:]\s*\d+\b").unwrap());
static PORT_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r":\b[3-6][0-9]{4}\b").unwrap());
static WS_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[ \t]+").unwrap());

/// Mask volatile tokens and collapse whitespace. Truncated to 256 bytes.
pub fn normalize(input: &str) -> String {
    let s = ANSI_RE.replace_all(input, "");
    let s = UUID_RE.replace_all(&s, "<UUID>");
    let s = HASH_RE.replace_all(&s, "<HASH>");
    let s = HEX_RE.replace_all(&s, "<HEX>");
    let s = TIMESTAMP_RE.replace_all(&s, "<TIMESTAMP>");
    let s = TMP_PATH_RE.replace_all(&s, "<TMP_PATH>");
    let s = PID_RE.replace_all(&s, "pid=<PID>");
    let s = PORT_RE.replace_all(&s, ":<PORT>");
    let mut s = WS_RE.replace_all(&s, " ").trim().to_string();
    if s.len() > 256 {
        s.truncate(256);
    }
    s
}

/// Derive a stable error signature from an observation: the first non-empty
/// line, normalized. Returns an empty string when there is no visible text.
pub fn error_signature(observation: &str) -> String {
    let line = observation
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or("");
    let mut masked = normalize(line);
    if masked.len() > 200 {
        masked.truncate(200);
    }
    masked
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn volatile_tokens_are_masked() {
        let masked = normalize("id 550e8400-e29b-41d4-a716-446655440000 at 0x7f8a9b1c");
        assert!(!masked.contains("550e8400"));
        assert!(!masked.contains("0x7f8a9b1c"));
        assert!(masked.contains("<UUID>"));
        assert!(masked.contains("<HEX>"));
    }

    #[test]
    fn error_signature_uses_first_non_empty_line() {
        assert_eq!(
            error_signature("\n  AssertionError: boom  \nmore"),
            "AssertionError: boom"
        );
        assert!(error_signature("").is_empty());
    }
}
