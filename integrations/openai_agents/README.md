# OpenAI Agents SDK integration

Microloop sits alongside the SDK's runner: it registers lifecycle hooks and maps
them into Microloop events. Nothing about `Runner.run` is forked or replaced.

```python
from integrations.openai_agents import MicroloopRuntime, OpenAIAgentsAdapter, run_with_microloop

runtime = MicroloopRuntime(adapter=OpenAIAgentsAdapter(), task="fix the failing test")
result = await run_with_microloop(agent, "fix the failing test", runtime=runtime)
```

## Mapping

| SDK hook | Microloop |
|---|---|
| `on_agent_start` | records the agent as a transcript segment |
| `on_llm_start` / `on_llm_end` | adds token usage to the adapter snapshot |
| `on_tool_start` / `on_tool_end` | one Microloop step: action, observation, error |
| `on_agent_end` | captures the final output |
| `on_handoff` | no-op today; handoffs are not yet modelled |

## Adaptations

`OpenAIAgentsAdapter` maps the provider-neutral tiers (`fast`, `balanced`,
`strong`) onto concrete model ids; change the map in
`DEFAULT_OPENAI_TIERS` to match your account. The three adaptations are real:
replan queues a message for the host to inject, escalation moves the tier (read
`adapter.model` for the next turn), and compaction runs the deterministic
compactor.

The SDK is optional. Construct the adapter and drive `MicroloopRuntime` directly
without it; `run_with_microloop` imports `agents` lazily and raises a clear error
if it is missing.
