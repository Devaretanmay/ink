use async_trait::async_trait;
use redis::AsyncCommands;
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};



#[async_trait]
pub trait BlocklistStore: Send + Sync {
    #[allow(dead_code)]
    async fn add_block(&self, session_id: &str, tool_call: &str) -> Result<(), String>;
    async fn is_blocked(&self, session_id: &str, tool_call: &str) -> Result<bool, String>;
}

pub struct InMemoryBlocklist {
    store: Arc<Mutex<HashMap<String, HashSet<String>>>>,
}

impl InMemoryBlocklist {
    pub fn new() -> Self {
        Self {
            store: Arc::new(Mutex::new(HashMap::new())),
        }
    }
}

#[async_trait]
impl BlocklistStore for InMemoryBlocklist {
    async fn add_block(&self, session_id: &str, tool_call: &str) -> Result<(), String> {
        let mut blocklist = self.store.lock().unwrap();
        blocklist
            .entry(session_id.to_string())
            .or_default()
            .insert(tool_call.to_string());
        Ok(())
    }

    async fn is_blocked(&self, session_id: &str, tool_call: &str) -> Result<bool, String> {
        let blocklist = self.store.lock().unwrap();
        Ok(blocklist
            .get(session_id)
            .is_some_and(|blocks| blocks.contains(tool_call)))
    }
}

pub struct RedisBlocklist {
    client: redis::Client,
}

impl RedisBlocklist {
    pub fn new(redis_url: &str) -> Result<Self, String> {
        let client = redis::Client::open(redis_url).map_err(|e| e.to_string())?;
        Ok(Self { client })
    }
}

#[async_trait]
impl BlocklistStore for RedisBlocklist {
    async fn add_block(&self, session_id: &str, tool_call: &str) -> Result<(), String> {
        let mut con = self
            .client
            .get_multiplexed_async_connection()
            .await
            .map_err(|e| e.to_string())?;
        
        let key = format!("microloop:blocklist:{}", session_id);
        let _: () = con.sadd(key, tool_call).await.map_err(|e| e.to_string())?;
        Ok(())
    }

    async fn is_blocked(&self, session_id: &str, tool_call: &str) -> Result<bool, String> {
        let mut con = self
            .client
            .get_multiplexed_async_connection()
            .await
            .map_err(|e| e.to_string())?;
            
        let key = format!("microloop:blocklist:{}", session_id);
        let is_member: bool = con.sismember(key, tool_call).await.map_err(|e| e.to_string())?;
        Ok(is_member)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_in_memory_blocklist_basic_operations() {
        let blocklist = InMemoryBlocklist::new();
        
        assert!(!blocklist.is_blocked("session_1", "tool_a").await.unwrap());
        
        blocklist.add_block("session_1", "tool_a").await.unwrap();
        assert!(blocklist.is_blocked("session_1", "tool_a").await.unwrap());
        
        assert!(!blocklist.is_blocked("session_2", "tool_a").await.unwrap());
        
        assert!(!blocklist.is_blocked("session_1", "tool_b").await.unwrap());
    }

    #[tokio::test]
    async fn test_in_memory_blocklist_multiple_blocks() {
        let blocklist = InMemoryBlocklist::new();
        
        blocklist.add_block("session_1", "tool_a").await.unwrap();
        blocklist.add_block("session_1", "tool_b").await.unwrap();
        blocklist.add_block("session_1", "tool_c").await.unwrap();
        
        assert!(blocklist.is_blocked("session_1", "tool_a").await.unwrap());
        assert!(blocklist.is_blocked("session_1", "tool_b").await.unwrap());
        assert!(blocklist.is_blocked("session_1", "tool_c").await.unwrap());
        assert!(!blocklist.is_blocked("session_1", "tool_d").await.unwrap());
    }

    #[tokio::test]
    async fn test_in_memory_blocklist_overwrite() {
        let blocklist = InMemoryBlocklist::new();
        
        blocklist.add_block("session_1", "tool_a").await.unwrap();
        blocklist.add_block("session_1", "tool_a").await.unwrap();
        
        assert!(blocklist.is_blocked("session_1", "tool_a").await.unwrap());
    }

    #[tokio::test]
    async fn test_in_memory_blocklist_concurrent_access() {
        let blocklist = Arc::new(InMemoryBlocklist::new());
        let mut handles = vec![];
        
        for i in 0..100 {
            let bl = blocklist.clone();
            handles.push(tokio::spawn(async move {
                let session_id = format!("session_{}", i % 10);
                let tool_name = format!("tool_{}", i % 5);
                bl.add_block(&session_id, &tool_name).await.unwrap();
                bl.is_blocked(&session_id, &tool_name).await.unwrap()
            }));
        }
        
        let results: Vec<bool> = futures::future::join_all(handles)
            .await
            .into_iter()
            .map(|r| r.unwrap())
            .collect();
        
        assert_eq!(results.len(), 100);
        assert!(results.iter().all(|&r| r));
    }

    #[tokio::test]
    #[ignore]
    async fn test_redis_blocklist_integration() {
        let redis_url = std::env::var("REDIS_URL").unwrap_or_default();
        if redis_url.is_empty() {
            eprintln!("Skipping Redis test - REDIS_URL not set");
            return;
        }
        
        let blocklist = RedisBlocklist::new(&redis_url).expect("Failed to connect to Redis");
        let test_session = format!("test_session_{}", uuid::Uuid::new_v4());
        
        assert!(!blocklist.is_blocked(&test_session, "tool_a").await.unwrap());
        blocklist.add_block(&test_session, "tool_a").await.unwrap();
        assert!(blocklist.is_blocked(&test_session, "tool_a").await.unwrap());
        
        let mut con = blocklist.client.get_multiplexed_async_connection().await.unwrap();
        let _: () = con.del(format!("microloop:blocklist:{}", test_session)).await.unwrap();
    }
}
