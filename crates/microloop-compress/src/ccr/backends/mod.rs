
pub mod in_memory;
#[cfg(feature = "redis")]
pub mod redis;
pub mod sqlite;

use std::path::PathBuf;

use thiserror::Error;

use crate::ccr::CcrStore;

#[cfg(feature = "redis")]
pub use self::redis::RedisCcrStore;
pub use in_memory::InMemoryCcrStore;
pub use sqlite::SqliteCcrStore;

#[derive(Debug, Clone)]
pub enum CcrBackendConfig {
    InMemory { capacity: usize, ttl_seconds: u64 },
    Sqlite { path: PathBuf, ttl_seconds: u64 },
    Redis {
        url: String,
        ttl_seconds: u64,
        key_prefix: Option<String>,
    },
}

impl CcrBackendConfig {
    pub fn sqlite_default(path: PathBuf) -> Self {
        Self::Sqlite {
            path,
            ttl_seconds: crate::ccr::DEFAULT_TTL.as_secs(),
        }
    }

    pub fn in_memory_default() -> Self {
        Self::InMemory {
            capacity: crate::ccr::DEFAULT_CAPACITY,
            ttl_seconds: crate::ccr::DEFAULT_TTL.as_secs(),
        }
    }
}

#[derive(Debug, Error)]
pub enum CcrBackendInitError {
    #[error("ccr sqlite backend init failed: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[cfg(feature = "redis")]
    #[error("ccr redis backend init failed: {0}")]
    Redis(::redis::RedisError),
    #[error(
        "ccr backend `{backend}` is not compiled in; rebuild with `--features {feature}` \
         or pick a different backend"
    )]
    UnsupportedBackend {
        backend: &'static str,
        feature: &'static str,
    },
}

#[cfg(feature = "redis")]
impl From<::redis::RedisError> for CcrBackendInitError {
    fn from(err: ::redis::RedisError) -> Self {
        Self::Redis(err)
    }
}

pub fn from_config(config: &CcrBackendConfig) -> Result<Box<dyn CcrStore>, CcrBackendInitError> {
    match config {
        CcrBackendConfig::InMemory {
            capacity,
            ttl_seconds,
        } => {
            let store = InMemoryCcrStore::with_capacity_and_ttl(
                *capacity,
                std::time::Duration::from_secs(*ttl_seconds),
            );
            tracing::info!(
                target = "ccr.backend",
                backend = "in_memory",
                capacity = *capacity,
                ttl_seconds = *ttl_seconds,
                "ccr_backend_initialized"
            );
            Ok(Box::new(store))
        }
        CcrBackendConfig::Sqlite { path, ttl_seconds } => {
            let store = SqliteCcrStore::open(path, *ttl_seconds)?;
            tracing::info!(
                target = "ccr.backend",
                backend = "sqlite",
                path = %path.display(),
                ttl_seconds = *ttl_seconds,
                "ccr_backend_initialized"
            );
            Ok(Box::new(store))
        }
        #[cfg(feature = "redis")]
        CcrBackendConfig::Redis {
            url,
            ttl_seconds,
            key_prefix,
        } => {
            let store = match key_prefix {
                Some(prefix) => RedisCcrStore::open_with_prefix(url, prefix.clone(), *ttl_seconds)?,
                None => RedisCcrStore::open(url, *ttl_seconds)?,
            };
            tracing::info!(
                target = "ccr.backend",
                backend = "redis",
                url = %url,
                ttl_seconds = *ttl_seconds,
                "ccr_backend_initialized"
            );
            Ok(Box::new(store))
        }
        #[cfg(not(feature = "redis"))]
        CcrBackendConfig::Redis { .. } => Err(CcrBackendInitError::UnsupportedBackend {
            backend: "redis",
            feature: "redis",
        }),
    }
}
