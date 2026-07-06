
use super::{Backend, Tokenizer};

#[derive(Debug, Clone, Copy)]
pub struct EstimatingCounter {
    chars_per_token: f64,
}

impl Default for EstimatingCounter {
    fn default() -> Self {
        Self {
            chars_per_token: 4.0,
        }
    }
}

impl EstimatingCounter {
    pub fn new(chars_per_token: f64) -> Self {
        assert!(
            chars_per_token > 0.0,
            "chars_per_token must be positive, got {chars_per_token}"
        );
        Self { chars_per_token }
    }

    pub fn chars_per_token(&self) -> f64 {
        self.chars_per_token
    }
}

impl Tokenizer for EstimatingCounter {
    fn count_text(&self, text: &str) -> usize {
        if text.is_empty() {
            return 0;
        }
        let chars = text.chars().count() as f64;
        let raw = (chars / self.chars_per_token + 0.5) as usize;
        raw.max(1)
    }

    fn backend(&self) -> Backend {
        Backend::Estimation
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_string_is_zero() {
        let est = EstimatingCounter::default();
        assert_eq!(est.count_text(""), 0);
    }

    #[test]
    fn default_is_four_chars_per_token() {
        let est = EstimatingCounter::default();
        assert_eq!(est.count_text("aaaa"), 1);
        assert_eq!(est.count_text("aaaaa"), 1);
        assert_eq!(est.count_text("aaaaaa"), 2);
        assert_eq!(est.count_text(&"a".repeat(40)), 10);
    }

    #[test]
    fn claude_density_matches_python() {
        let est = EstimatingCounter::new(3.5);
        assert_eq!(est.count_text(&"a".repeat(35)), 10);
        assert_eq!(est.count_text(&"a".repeat(36)), 10);
        assert_eq!(est.count_text(&"a".repeat(38)), 11);
    }

    #[test]
    fn unicode_uses_char_count_not_bytes() {
        let est = EstimatingCounter::default();
        assert_eq!(est.count_text("héllo"), 1);
        assert_eq!(est.count_text("🦀🦀🦀🦀"), 1);
    }

    #[test]
    fn min_is_one_for_non_empty_input() {
        let est = EstimatingCounter::default();
        assert_eq!(est.count_text("a"), 1);
        assert_eq!(est.count_text("ab"), 1);
        assert_eq!(est.count_text("abc"), 1);
        assert_eq!(est.count_text("aaaaaa"), 2);
    }

    #[test]
    fn deterministic() {
        let est = EstimatingCounter::default();
        let s = "the quick brown fox jumps over the lazy dog";
        let a = est.count_text(s);
        let b = est.count_text(s);
        assert_eq!(a, b);
    }

    #[test]
    fn very_long_input_does_not_overflow() {
        let est = EstimatingCounter::default();
        let s = "a".repeat(1_000_000);
        assert_eq!(est.count_text(&s), 250_000);
    }

    #[test]
    #[should_panic(expected = "chars_per_token must be positive")]
    fn rejects_zero_density() {
        let _ = EstimatingCounter::new(0.0);
    }

    #[test]
    #[should_panic(expected = "chars_per_token must be positive")]
    fn rejects_negative_density() {
        let _ = EstimatingCounter::new(-1.0);
    }

    #[test]
    fn backend_is_estimation() {
        let est = EstimatingCounter::default();
        assert_eq!(est.backend(), Backend::Estimation);
    }
}
