# Microloop

Local trajectory monitoring for autonomous agents.

Microloop is moving from a tool-call circuit breaker to an evidence-based
non-progress detector. It observes actions, results and optional verifier metrics;
the host agent owns execution and decides what to do with the signal.

**Experimental. No measured task-completion lift is claimed.**

The next milestone is **Experiment 001**: can monitoring plus targeted recovery
improve coding-agent completion under the same model, tools and total budget?

- [Product requirements and validation protocol](docs/PRD.md)
- [What was kept, removed and deferred](docs/REPOSITORY-SCOPE.md)
- [Contributing](CONTRIBUTING.md)

The existing Rust `MicroloopState` / `verify` and Python `Microloop.verify` APIs
remain as legacy call-repetition checks. They do not infer goal progress.
The proxy, framework adapters and website are removed from this experimental
branch. Compression/CCR is preserved outside the active workspace.

```sh
cargo test -p microloop
cargo run --example basic
```

Apache-2.0 licensed. See [LICENSE](LICENSE).
