
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ImportanceContext {
    Text,
    Search,
    Diff,
    Log,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ImportanceCategory {
    Error,
    Warning,
    Importance,
    Security,
    Markdown,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ImportanceSignal {
    pub category: Option<ImportanceCategory>,
    pub priority: f32,
    pub confidence: f32,
}

impl ImportanceSignal {
    pub const fn neutral() -> Self {
        Self {
            category: None,
            priority: 0.0,
            confidence: 0.0,
        }
    }

    pub const fn matched(category: ImportanceCategory, priority: f32, confidence: f32) -> Self {
        Self {
            category: Some(category),
            priority,
            confidence,
        }
    }

    pub fn is_match(&self) -> bool {
        self.category.is_some()
    }
}

pub trait LineImportanceDetector: Send + Sync {
    fn score(&self, line: &str, ctx: ImportanceContext) -> ImportanceSignal;
}
