
#![cfg(feature = "redis")]

use redis::Commands;

use crate::ccr::CcrStore;

const DEFAULT_KEY_PREFIX: &str = "ccr";

pub struct RedisCcrStore {
    client: redis::Client,
    key_prefix: String,
    default_ttl_seconds: u64,
}

impl RedisCcrStore {
    pub fn open(url: &str, default_ttl_seconds: u64) -> redis::RedisResult<Self> {
        Self::open_with_prefix(url, DEFAULT_KEY_PREFIX.to_string(), default_ttl_seconds)
    }

    pub fn open_with_prefix(
        url: &str,
        key_prefix: String,
        default_ttl_seconds: u64,
    ) -> redis::RedisResult<Self> {
        let client = redis::Client::open(url)?;
        let mut conn = client.get_connection()?;
        let _: String = redis::cmd("PING").query(&mut conn)?;
        Ok(Self {
            client,
            key_prefix,
            default_ttl_seconds,
        })
    }

    fn key_for(&self, hash: &str) -> String {
        format!("{}:{}", self.key_prefix, hash)
    }

    pub fn default_ttl_seconds(&self) -> u64 {
        self.default_ttl_seconds
    }
}

impl CcrStore for RedisCcrStore {
    fn put_with_version(&self, hash: &str, payload: &str, schema_version: u8) {
        let key = self.key_for(hash);
        let mut conn = match self.client.get_connection() {
            Ok(c) => c,
            Err(err) => {
                tracing::warn!(
                    target = "ccr.redis",
                    hash = %hash,
                    error = %err,
                    "ccr_redis_connect_failed_on_put"
                );
                return;
            }
        };
        let data = serde_json::json!({"v": schema_version, "d": payload}).to_string();
        let res: redis::RedisResult<()> =
            conn.set_ex(&key, data.as_bytes(), self.default_ttl_seconds);
        if let Err(err) = res {
            tracing::warn!(
                target = "ccr.redis",
                hash = %hash,
                error = %err,
                "ccr_redis_put_failed"
            );
        }
    }

    fn get_with_version(&self, hash: &str) -> Option<(String, u8)> {
        let key = self.key_for(hash);
        let mut conn = match self.client.get_connection() {
            Ok(c) => c,
            Err(err) => {
                tracing::warn!(
                    target = "ccr.redis",
                    hash = %hash,
                    error = %err,
                    "ccr_redis_connect_failed_on_get"
                );
                return None;
            }
        };
        let bytes: redis::RedisResult<Option<Vec<u8>>> = conn.get(&key);
        match bytes {
            Ok(Some(bytes)) => {
                let raw = String::from_utf8(bytes).ok()?;
                if let Ok(wrapped) = serde_json::from_str::<serde_json::Value>(&raw) {
                    if let (Some(v), Some(d)) = (
                        wrapped.get("v").and_then(|v| v.as_u64()),
                        wrapped.get("d").and_then(|d| d.as_str()),
                    ) {
                        return Some((d.to_string(), v.min(255) as u8));
                    }
                }
                Some((raw, 0))
            }
            Ok(None) => None,
            Err(err) => {
                tracing::warn!(
                    target = "ccr.redis",
                    hash = %hash,
                    error = %err,
                    "ccr_redis_get_failed"
                );
                None
            }
        }
    }

    fn len(&self) -> usize {
        0
    }
}
