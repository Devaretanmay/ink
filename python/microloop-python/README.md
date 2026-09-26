# Microloop Python

Python bindings to the local Rust trajectory engine. This source branch is
experimental; published packages may not contain the new monitor API.
See the repository's docs/PRD.md for scope and evidence requirements.

The existing `Microloop(yaml_config).verify(tool_name, arguments_json)` remains
available for compatibility. It checks call repetition, not task completion.

Build from this repository with maturin. No provider credentials are needed for
local monitoring or detector tests.
