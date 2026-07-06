use rusqlite::{params, Connection};
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

}
