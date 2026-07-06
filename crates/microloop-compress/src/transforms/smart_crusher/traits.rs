
use serde_json::Value;


pub trait Constraint: Send + Sync {
    fn name(&self) -> &str;

    fn must_keep(&self, items: &[Value], item_strings: Option<&[String]>) -> Vec<usize>;
}


#[derive(Debug, Clone)]
pub struct CrushEvent {
    pub strategy: String,
    pub input_bytes: usize,
    pub output_bytes: usize,
    pub elapsed_ns: u64,
    pub was_modified: bool,
}

pub trait Observer: Send + Sync {
    fn name(&self) -> &str {
        std::any::type_name::<Self>()
    }

    fn on_event(&self, event: &CrushEvent);
}


pub use crate::relevance::RelevanceScorer as Scorer;

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    struct AlwaysKeepFirst;
    impl Constraint for AlwaysKeepFirst {
        fn name(&self) -> &str {
            "always_keep_first"
        }
        fn must_keep(&self, items: &[Value], _: Option<&[String]>) -> Vec<usize> {
            if items.is_empty() {
                Vec::new()
            } else {
                vec![0]
            }
        }
    }

    #[test]
    fn constraint_returns_indices_in_bounds() {
        let items = vec![json!({"a": 1}), json!({"a": 2})];
        let c = AlwaysKeepFirst;
        let kept = c.must_keep(&items, None);
        assert_eq!(kept, vec![0]);
        assert_eq!(c.name(), "always_keep_first");
    }

    #[test]
    fn constraint_handles_empty_input() {
        let kept = AlwaysKeepFirst.must_keep(&[], None);
        assert!(kept.is_empty());
    }

    #[derive(Default)]
    struct CountingObserver {
        count: Arc<AtomicUsize>,
    }
    impl Observer for CountingObserver {
        fn on_event(&self, _: &CrushEvent) {
            self.count.fetch_add(1, Ordering::SeqCst);
        }
    }

    #[test]
    fn observer_event_carries_strategy_and_sizes() {
        let observer = CountingObserver::default();
        let event = CrushEvent {
            strategy: "smart_sample(30->15)".to_string(),
            input_bytes: 1000,
            output_bytes: 500,
            elapsed_ns: 12_345,
            was_modified: true,
        };
        observer.on_event(&event);
        assert_eq!(observer.count.load(Ordering::SeqCst), 1);
    }
}
