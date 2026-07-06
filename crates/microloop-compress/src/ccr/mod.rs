
pub mod backends;

use std::time::Duration;

pub use backends::{from_config, CcrBackendConfig, CcrBackendInitError, InMemoryCcrStore};

pub trait CcrStore: Send + Sync {
    fn put(&self, hash: &str, payload: &str);

    fn get(&self, hash: &str) -> Option<String>;

    fn len(&self) -> usize;

    fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

pub const DEFAULT_CAPACITY: usize = 1000;

pub const DEFAULT_TTL: Duration = Duration::from_secs(1800);

pub fn compute_key(payload: &[u8]) -> String {
    let h = blake3::hash(payload);
    let hex = h.to_hex();
    hex.as_str()[..24].to_string()
}

pub fn marker_for(hash: &str) -> String {
    format!("<<ccr:{hash}>>")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compute_key_is_24_hex_chars() {
        let k = compute_key(b"hello world");
        assert_eq!(k.len(), 24);
        assert!(k
            .chars()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()));
    }

    #[test]
    fn compute_key_is_deterministic() {
        let a = compute_key(b"the same payload");
        let b = compute_key(b"the same payload");
        assert_eq!(a, b);
    }

    #[test]
    fn compute_key_diverges_for_different_payloads() {
        let a = compute_key(b"alpha");
        let b = compute_key(b"beta");
        assert_ne!(a, b);
    }

    #[test]
    fn marker_format_is_pinned() {
        assert_eq!(marker_for("abc123"), "<<ccr:abc123>>");
    }
}
