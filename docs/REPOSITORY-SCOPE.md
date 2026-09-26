# Repository reset for Experiment 001

Baseline: `a9977bde807ac04d6e2e1c3fb370be9285288f75`.
The removed implementation remains recoverable from Git history.

| Component | Decision | Reason |
|---|---|---|
| Rust call-loop API and C exports | Keep for compatibility | Existing integrations can migrate incrementally |
| Rust trajectory monitor | Build | New product primitive |
| Python binding | Keep and extend | First experiment integration |
| Compression / CCR crate | Retain, exclude from workspace | Useful prior work; not this experiment's treatment |
| Reverse proxy | Remove | Owns interception and compression beyond current scope |
| WASM packaging | Remove | Extra platform without a validation user |
| MCP, LangGraph, LiteLLM adapters/extras | Remove | Premature integration breadth |
| Compression tool schemas/Python wrappers | Remove | No active consumer after proxy removal |
| Mock upstream, Ollama scripts, promotional demos | Remove | Do not establish real task completion |
| Combined compression/loop timing binary | Remove | Not evidence for recovery lift |
| Landing page, assets, Pages deployment | Remove | Old positioning and unsupported outcome/performance claims |
| README and contributor instructions | Rewrite | Describe actual support and experimental status |

This is a breaking source-tree cleanup on an experimental branch, not a package
release. Previously published packages are unchanged. The old `Microloop.verify`
API still counts call repetition without environment evidence; do not use it as
the new experiment's progress monitor. No migration of deleted proxy settings is
provided. Legacy compression has no active CI support until deliberately restored.
