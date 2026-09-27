"""OpenAI Agents SDK integration.

The SDK already manages turns, tools, sessions and tracing, so Microloop sits
*alongside* that runtime rather than replacing it. We register the SDK's
lifecycle hooks (``on_agent_start``, ``on_llm_start``/``on_llm_end``,
``on_tool_start``/``on_tool_end``, ``on_agent_end``) and map them into Microloop
events. Nothing about the runner is forked.

The SDK is an optional dependency. This module imports it lazily: without it you
can still construct the adapter and drive :class:`MicroloopRuntime` directly
(which is what the tests do), and the hook class falls back to a plain object so
the code stays importable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from microloop import Episode, Monitor, RuntimeAction, RuntimeSession, ScoredController
from microloop.store import EpisodeStore

from integrations.openai_agents.adapter import OpenAIAgentsAdapter

__all__ = [
    "MicroloopRunHooks",
    "MicroloopRuntime",
    "OpenAIAgentsAdapter",
    "run_with_microloop",
]


def _run_hooks_base() -> type:
    """Subclass the SDK's ``RunHooks`` when it is installed, else ``object``."""
    try:  # pragma: no cover - depends on the optional SDK
        from agents import RunHooks

        return RunHooks
    except Exception:  # noqa: BLE001 - absent or incompatible SDK
        return object


class MicroloopRuntime:
    """Owns the Microloop session and maps SDK lifecycle events to it."""

    def __init__(
        self,
        *,
        adapter: OpenAIAgentsAdapter,
        controller: ScoredController | None = None,
        episode: Episode | None = None,
        store: EpisodeStore | None = None,
        task: str | None = None,
    ) -> None:
        self.adapter = adapter
        self.controller = controller or ScoredController(
            stalled=RuntimeAction.Replan,
            regressing=RuntimeAction.Stop,
            cooldown_steps=1,
            max_interventions=1_000,
            capabilities=adapter.capabilities(),
        )
        self.episode = episode if episode is not None else Episode(goal=task)
        self.store = store
        self.task = task
        self.session = RuntimeSession(
            Monitor(controller=self.controller), adapter, episode=self.episode
        )
        self._step = 0
        self.result: Any = None

    def observe(
        self,
        *,
        action: str,
        observation: str,
        metrics: dict[str, float] | None = None,
        metadata: dict[str, str] | None = None,
    ):
        """Record one mapped event and let the controller decide."""
        self._step += 1
        decision = self.session.observe(
            action=action,
            observation=observation,
            metrics=metrics,
            metadata=metadata,
            step=self._step,
        )
        return decision

    def pending_replan(self) -> str | None:
        """The replan message the host should inject, if one was queued."""
        return self.adapter.take_replan()

    def finish(
        self,
        *,
        success: bool,
        verifier: str | None = None,
        score: float | None = None,
        run_mode: str = "real",
        arm: str = "adaptive",
    ) -> dict[str, Any]:
        """Close the episode and persist it when a store is attached."""
        self.session.finish("completed" if success else "failed")
        if self.store is not None:
            self.store.record(
                self.episode,
                task=self.task,
                arm=arm,
                run_mode=run_mode,
                success=success,
                verifier=verifier,
                score=score,
            )
        return self.episode.summary()


class MicroloopRunHooks(_run_hooks_base()):  # type: ignore[misc, valid-type]
    """SDK lifecycle hooks that forward events to a :class:`MicroloopRuntime`.

    Construct with a runtime and pass the instance to ``Runner.run(hooks=...)``.
    Every method is async because the SDK's are.
    """

    def __init__(self, runtime: MicroloopRuntime) -> None:
        self.runtime = runtime

    async def on_agent_start(self, context: Any, agent: Any) -> None:  # noqa: ARG002
        name = getattr(agent, "name", "agent")
        self.runtime.adapter.record_segment("task", f"agent start: {name}")

    async def on_llm_end(self, context: Any, agent: Any, response: Any) -> None:  # noqa: ARG002
        usage = getattr(response, "usage", None)
        self.runtime.adapter.add_usage(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )

    async def on_tool_end(
        self, context: Any, agent: Any, tool: Any, result: Any  # noqa: ARG002
    ) -> None:
        name = getattr(tool, "name", "tool")
        arguments = getattr(context, "tool_arguments", None)
        action = f"{name} {arguments}".strip() if arguments else name
        observation = str(result)
        failed = isinstance(result, dict) and result.get("error") is not None
        metadata = {"error": observation.splitlines()[0][:200]} if failed else None
        self.runtime.adapter.record_segment("tool", observation)
        self.runtime.observe(
            action=action,
            observation=observation,
            metrics={"exit_code": 1.0 if failed else 0.0},
            metadata=metadata,
        )

    async def on_agent_end(self, context: Any, agent: Any, output: Any) -> None:  # noqa: ARG002
        self.runtime.result = output


@dataclass
class RunOutcome:
    """Minimal view of an SDK run, filled in when the SDK is present."""

    result: Any
    final_output: Any


async def run_with_microloop(
    agent: Any,
    task: str,
    *,
    runtime: MicroloopRuntime,
    run_config: Any = None,
    max_turns: int | None = None,
) -> RunOutcome:
    """Run an SDK agent with Microloop observing through the SDK's hooks.

    The SDK runner is untouched: we only supply hooks. Real model switching and
    context compaction are applied by the adapter between turns; a queued replan
    message is available from :meth:`MicroloopRuntime.pending_replan` for the
    host to inject through its own context mechanism.
    """
    try:  # pragma: no cover - requires the optional SDK
        from agents import Runner
    except ImportError as error:
        raise RuntimeError(
            "run_with_microloop needs the 'openai-agents' package; "
            "install it with `pip install openai-agents`"
        ) from error

    hooks = MicroloopRunHooks(runtime)
    result = await Runner.run(
        agent, task, hooks=hooks, run_config=run_config, max_turns=max_turns
    )
    return RunOutcome(result=result, final_output=getattr(result, "final_output", None))
