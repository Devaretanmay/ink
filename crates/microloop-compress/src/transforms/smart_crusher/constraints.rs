
use serde_json::Value;

use super::outliers::{detect_error_items_for_preservation, detect_structural_outliers};
use super::traits::Constraint;


#[derive(Debug, Default, Clone, Copy)]
pub struct KeepErrorsConstraint;

impl Constraint for KeepErrorsConstraint {
    fn name(&self) -> &str {
        "keep_errors"
    }

    fn must_keep(&self, items: &[Value], item_strings: Option<&[String]>) -> Vec<usize> {
        detect_error_items_for_preservation(items, item_strings)
    }
}


#[derive(Debug, Default, Clone, Copy)]
pub struct KeepStructuralOutliersConstraint;

impl Constraint for KeepStructuralOutliersConstraint {
    fn name(&self) -> &str {
        "keep_structural_outliers"
    }

    fn must_keep(&self, items: &[Value], _item_strings: Option<&[String]>) -> Vec<usize> {
        detect_structural_outliers(items)
    }
}


pub fn default_oss_constraints() -> Vec<Box<dyn Constraint>> {
    vec![
        Box::new(KeepErrorsConstraint),
        Box::new(KeepStructuralOutliersConstraint),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn keep_errors_constraint_finds_error_items() {
        let mut items: Vec<Value> = (0..9).map(|i| json!({"id": i, "status": "ok"})).collect();
        items.push(json!({"id": 9, "status": "ERROR", "msg": "FATAL: boom"}));
        let kept = KeepErrorsConstraint.must_keep(&items, None);
        assert!(kept.contains(&9), "error item must be flagged for keep");
    }

    #[test]
    fn keep_errors_constraint_uses_item_strings_when_provided() {
        let items: Vec<Value> = vec![json!({"a": 1}), json!({"a": "exception"})];
        let strings: Vec<String> = items
            .iter()
            .map(|v| serde_json::to_string(v).unwrap())
            .collect();
        let with_cache = KeepErrorsConstraint.must_keep(&items, Some(&strings));
        let without_cache = KeepErrorsConstraint.must_keep(&items, None);
        assert_eq!(with_cache, without_cache);
        assert!(with_cache.contains(&1));
    }

    #[test]
    fn keep_structural_outliers_constraint_returns_indices() {
        let mut items: Vec<Value> = (0..20)
            .map(|i| json!({"id": i, "kind": "common"}))
            .collect();
        items.push(json!({"id": 20, "kind": "common", "rare_extra_field": "x"}));
        let kept = KeepStructuralOutliersConstraint.must_keep(&items, None);
        assert!(
            kept.contains(&20),
            "item with rare field should be a structural outlier"
        );
    }

    #[test]
    fn default_oss_constraints_returns_two() {
        let cs = default_oss_constraints();
        assert_eq!(cs.len(), 2);
        let names: Vec<&str> = cs.iter().map(|c| c.name()).collect();
        assert_eq!(names, vec!["keep_errors", "keep_structural_outliers"]);
    }

    #[test]
    fn constraints_handle_empty_array() {
        assert!(KeepErrorsConstraint.must_keep(&[], None).is_empty());
        assert!(KeepStructuralOutliersConstraint
            .must_keep(&[], None)
            .is_empty());
    }
}
