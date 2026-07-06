
use sha2::{Digest, Sha256};

pub fn hash_field_name(field_name: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(field_name.as_bytes());
    let digest = hasher.finalize();
    let hex = format!("{:x}", digest);
    hex[..8].to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_python_sha256_truncated_to_8() {
        assert_eq!(hash_field_name("customer_id"), "1e38d67d");
    }

    #[test]
    fn empty_string() {
        assert_eq!(hash_field_name(""), "e3b0c442");
    }

    #[test]
    fn unicode_field_name() {
        assert_eq!(hash_field_name("café"), "850f7dc4");
    }

    #[test]
    fn deterministic() {
        assert_eq!(hash_field_name("test"), hash_field_name("test"));
    }

    #[test]
    fn output_length_is_8() {
        assert_eq!(hash_field_name("a").len(), 8);
        assert_eq!(hash_field_name(&"x".repeat(1000)).len(), 8);
    }
}
