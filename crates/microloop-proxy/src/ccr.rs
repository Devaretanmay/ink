use rusqlite::{params, Connection, OptionalExtension};
use std::sync::{Arc, Mutex};

pub struct CcrStore {
    conn: Arc<Mutex<Connection>>,
}

impl CcrStore {
    pub fn new(db_path: &str) -> Result<Self, rusqlite::Error> {
        let conn = Connection::open(db_path)?;

        conn.execute(
            "CREATE TABLE IF NOT EXISTS ccr (
                hash TEXT PRIMARY KEY,
                original_content TEXT NOT NULL
            )",
            [],
        )?;

        Ok(Self {
            conn: Arc::new(Mutex::new(conn)),
        })
    }

    pub fn insert(&self, hash: &str, content: &str) -> Result<(), rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT OR IGNORE INTO ccr (hash, original_content) VALUES (?1, ?2)",
            params![hash, content],
        )?;
        Ok(())
    }

    pub fn get(&self, hash: &str) -> Option<String> {
        let conn = self.conn.lock().unwrap();
        conn.query_row(
            "SELECT original_content FROM ccr WHERE hash = ?1 OR (LENGTH(?1) < 64 AND hash LIKE (?1 || '%'))",
            params![hash],
            |row| row.get::<_, String>(0),
        )
        .optional()
        .unwrap_or(None)
    }
}
