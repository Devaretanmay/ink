
use super::traits::{CrushEvent, Observer};

#[derive(Debug, Default, Clone, Copy)]
pub struct TracingObserver;

impl Observer for TracingObserver {
    fn name(&self) -> &str {
        "tracing"
    }

    fn on_event(&self, event: &CrushEvent) {
        tracing::debug!(
            target: "headroom::smart_crusher",
            strategy = %event.strategy,
            input_bytes = event.input_bytes,
            output_bytes = event.output_bytes,
            elapsed_ns = event.elapsed_ns,
            was_modified = event.was_modified,
            "smart_crusher.crush emitted",
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tracing_observer_does_not_panic_on_event() {
        let event = CrushEvent {
            strategy: "passthrough".to_string(),
            input_bytes: 100,
            output_bytes: 100,
            elapsed_ns: 0,
            was_modified: false,
        };
        TracingObserver.on_event(&event);
    }

    #[test]
    fn tracing_observer_name_is_stable() {
        assert_eq!(TracingObserver.name(), "tracing");
    }
}
