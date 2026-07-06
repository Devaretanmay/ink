
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OptionalExtension};

use crate::ccr::CcrStore;

pub struct SqliteCcrStore {
    conn: Mutex<Connection>,
    default_ttl_seconds: u64,
    path: PathBuf,
}

impl SqliteCcrStore {
    pub fn open(path: impl AsRef<Path>, default_ttl_seconds: u64) -> rusqlite::Result<Self> {
        let path_buf = path.as_ref().to_path_buf();
        let conn = Connection::open(&path_buf)?;

        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "synchronous", "NORMAL")?;

        conn.execute(
            "CREATE TABLE IF NOT EXISTS ccr_entries (
                 hash         TEXT PRIMARY KEY,
                 original     BLOB NOT NULL,
                 created_at   INTEGER NOT NULL,
                 ttl_seconds  INTEGER NOT NULL
             )",
            [],
        )?;

        Ok(Self {
            conn: Mutex::new(conn),
            default_ttl_seconds,
            path: path_buf,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn default_ttl_seconds(&self) -> u64 {
        self.default_ttl_seconds
    }

    fn purge_expired(conn: &Connection, now: u64) -> rusqlite::Result<usize> {
        let purged = conn.execute(
            "DELETE FROM ccr_entries WHERE created_at + ttl_seconds <= ?1",
            params![now as i64],
        )?;
        Ok(purged)
    }

    fn now_unix_seconds() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0)
    }
}

impl CcrStore for SqliteCcrStore {
    fn put(&self, hash: &str, payload: &str) {
        let now = Self::now_unix_seconds();
        let conn = self.conn.lock().expect("ccr sqlite mutex poisoned");
        let res = conn.execute(
            "INSERT INTO ccr_entries (hash, original, created_at, ttl_seconds)
             VALUES (?1, ?2, ?3, ?4)
             ON CONFLICT(hash) DO UPDATE SET
                 original    = excluded.original,
                 created_at  = excluded.created_at,
                 ttl_seconds = excluded.ttl_seconds",
            params![
                hash,
                payload.as_bytes(),
                now as i64,
                self.default_ttl_seconds as i64,
            ],
        );
        if let Err(err) = res {
            tracing::warn!(
                target = "ccr.sqlite",
                hash = %hash,
                error = %err,
                "ccr_sqlite_put_failed"
            );
        }
    }

    fn get(&self, hash: &str) -> Option<String> {
        let now = Self::now_unix_seconds();
        let conn = self.conn.lock().expect("ccr sqlite mutex poisoned");

        if let Err(err) = Self::purge_expired(&conn, now) {
            tracing::warn!(
                target = "ccr.sqlite",
                error = %err,
                "ccr_sqlite_purge_failed"
            );
        }

        let row: Option<Vec<u8>> = conn
            .query_row(
                "SELECT original FROM ccr_entries
                 WHERE hash = ?1 AND created_at + ttl_seconds > ?2",
                params![hash, now as i64],
                |r| r.get::<_, Vec<u8>>(0),
            )
            .optional()
            .unwrap_or_else(|err| {
                tracing::warn!(
                    target = "ccr.sqlite",
                    hash = %hash,
                    error = %err,
                    "ccr_sqlite_get_failed"
                );
                None
            });

        row.and_then(|bytes| String::from_utf8(bytes).ok())
    }

    fn len(&self) -> usize {
        let conn = self.conn.lock().expect("ccr sqlite mutex poisoned");
        conn.query_row("SELECT COUNT(*) FROM ccr_entries", [], |r| {
            r.get::<_, i64>(0)
        })
        .map(|n| n.max(0) as usize)
        .unwrap_or(0)
    }
}
