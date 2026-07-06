
use std::sync::Arc;

use crate::ccr::{CcrStore, InMemoryCcrStore};
use crate::relevance::{HybridScorer, RelevanceScorer};
use crate::transforms::anchor_selector::{AnchorConfig, AnchorSelector};

use super::analyzer::SmartAnalyzer;
use super::compaction::CompactionStage;
use super::config::SmartCrusherConfig;
use super::constraints::default_oss_constraints;
use super::crusher::SmartCrusher;
use super::observer::TracingObserver;
use super::traits::{Constraint, Observer};

pub struct SmartCrusherBuilder {
    config: SmartCrusherConfig,
    anchor_config: Option<AnchorConfig>,
    scorer: Option<Box<dyn RelevanceScorer + Send + Sync>>,
    constraints: Vec<Box<dyn Constraint>>,
    observers: Vec<Box<dyn Observer>>,
    compaction: Option<CompactionStage>,
    ccr_store: Option<Arc<dyn CcrStore>>,
}

impl SmartCrusherBuilder {
    pub fn new(config: SmartCrusherConfig) -> Self {
        SmartCrusherBuilder {
            config,
            anchor_config: None,
            scorer: None,
            constraints: Vec::new(),
            observers: Vec::new(),
            compaction: None,
            ccr_store: None,
        }
    }

    pub fn anchor_config(mut self, cfg: AnchorConfig) -> Self {
        self.anchor_config = Some(cfg);
        self
    }

    pub fn with_scorer(mut self, scorer: Box<dyn RelevanceScorer + Send + Sync>) -> Self {
        self.scorer = Some(scorer);
        self
    }

    pub fn add_constraint(mut self, c: Box<dyn Constraint>) -> Self {
        self.constraints.push(c);
        self
    }

    pub fn add_default_oss_constraints(mut self) -> Self {
        self.constraints.extend(default_oss_constraints());
        self
    }

    pub fn add_observer(mut self, o: Box<dyn Observer>) -> Self {
        self.observers.push(o);
        self
    }

    pub fn with_default_oss_setup(self) -> Self {
        self.with_scorer(Box::<HybridScorer>::default())
            .add_default_oss_constraints()
            .add_observer(Box::new(TracingObserver))
    }

    pub fn with_compaction(mut self, stage: CompactionStage) -> Self {
        self.compaction = Some(stage);
        self
    }

    pub fn with_default_compaction(self) -> Self {
        self.with_compaction(CompactionStage::default_csv_schema())
    }

    pub fn with_ccr_store(mut self, store: Arc<dyn CcrStore>) -> Self {
        self.ccr_store = Some(store);
        self
    }

    pub fn with_default_ccr_store(self) -> Self {
        self.with_ccr_store(Arc::new(InMemoryCcrStore::new()))
    }

    pub fn build(self) -> SmartCrusher {
        let analyzer = SmartAnalyzer::new(self.config.clone());
        let anchor_selector = AnchorSelector::new(self.anchor_config.unwrap_or_default());
        let scorer = self
            .scorer
            .unwrap_or_else(|| Box::<HybridScorer>::default());
        SmartCrusher::from_parts(
            self.config,
            anchor_selector,
            scorer,
            analyzer,
            self.constraints,
            self.observers,
            self.compaction,
            self.ccr_store,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::transforms::smart_crusher::traits::{Constraint, CrushEvent, Observer};
    use serde_json::Value;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    struct MarkerConstraint {
        name: &'static str,
    }
    impl Constraint for MarkerConstraint {
        fn name(&self) -> &str {
            self.name
        }
        fn must_keep(&self, _: &[Value], _: Option<&[String]>) -> Vec<usize> {
            Vec::new()
        }
    }

    struct MarkerObserver {
        count: Arc<AtomicUsize>,
    }
    impl Observer for MarkerObserver {
        fn on_event(&self, _: &CrushEvent) {
            self.count.fetch_add(1, Ordering::SeqCst);
        }
    }

    #[test]
    fn empty_builder_builds_with_default_scorer() {
        let crusher = SmartCrusherBuilder::new(SmartCrusherConfig::default()).build();
        assert!(crusher.constraints.is_empty());
        assert!(crusher.observers.is_empty());
    }

    #[test]
    fn add_default_oss_constraints_appends_two() {
        let crusher = SmartCrusherBuilder::new(SmartCrusherConfig::default())
            .add_default_oss_constraints()
            .build();
        assert_eq!(crusher.constraints.len(), 2);
        let names: Vec<&str> = crusher.constraints.iter().map(|c| c.name()).collect();
        assert_eq!(names, vec!["keep_errors", "keep_structural_outliers"]);
    }

    #[test]
    fn add_constraint_preserves_order() {
        let crusher = SmartCrusherBuilder::new(SmartCrusherConfig::default())
            .add_constraint(Box::new(MarkerConstraint { name: "first" }))
            .add_constraint(Box::new(MarkerConstraint { name: "second" }))
            .add_constraint(Box::new(MarkerConstraint { name: "third" }))
            .build();
        let names: Vec<&str> = crusher.constraints.iter().map(|c| c.name()).collect();
        assert_eq!(names, vec!["first", "second", "third"]);
    }

    #[test]
    fn with_default_oss_setup_yields_two_constraints_one_observer() {
        let crusher = SmartCrusherBuilder::new(SmartCrusherConfig::default())
            .with_default_oss_setup()
            .build();
        assert_eq!(crusher.constraints.len(), 2);
        assert_eq!(crusher.observers.len(), 1);
    }

    #[test]
    fn builder_observer_fires_on_crush() {
        let counter = Arc::new(AtomicUsize::new(0));
        let crusher = SmartCrusherBuilder::new(SmartCrusherConfig::default())
            .add_observer(Box::new(MarkerObserver {
                count: counter.clone(),
            }))
            .build();
        let _ = crusher.crush(r#"[1, 2, 3]"#, "", 1.0);
        assert_eq!(counter.load(Ordering::SeqCst), 1);
    }
}
