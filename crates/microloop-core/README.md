# microloop-core

Deterministic progress detection and recovery policy for autonomous agents.

Part of [Microloop](https://github.com/Devaretanmay/microloop). The core is a
pure Rust library: no network, no I/O, no agent execution. See the root README
for the product overview.

```rust
use microloop_core::{Event, InterventionAction, Monitor};

let mut monitor = Monitor::new();
let mut event = Event::new(1, "pytest tests/", "1 failed, 4 passed");
event.metrics = Some([("exit_code".to_string(), 1.0)].into_iter().collect());

let decision = monitor.observe(event)?;
if decision.intervention != InterventionAction::Observe {
    eprintln!("{}", decision.feedback.unwrap_or_default());
}
```
