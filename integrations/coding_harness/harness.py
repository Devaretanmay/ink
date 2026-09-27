"""A small coding harness whose runtime Microloop controls.

The harness owns everything concrete: the task, the workspace, the shell, the
tests, the model call and the tool loop. Microloop owns progress, runtime state
and the adaptation decision. That split means the controller can be tested
against real execution without Microloop ever knowing which provider is behind
the model.

The provider is pluggable, so the same harness runs offline (scripted) for tests
and dry runs, or against Anthropic for the real experiment.
"""
from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from microloop import Episode, RuntimeSession

from integrations.coding_harness.providers import (
    AgentBehaviour,
    BudgetExhausted,
    ModelReply,
    Provider,
)

__all__ = ["CodingHarness", "RunResult", "SYSTEM_PROMPT", "Task"]

#: The harness's standing instructions. A real agent needs to be told what the
#: tools do and that it is judged by the verifier, otherwise it optimizes for
#: looking busy rather than for passing.
SYSTEM_PROMPT = (
    "You are a coding agent working in a sandboxed workspace.\n"
    "Use write_file to change source, read_file to inspect it, and run_tests "
    "to verify.\n"
    "run_tests is the only measure of success. Read its output and act on it."
)


@dataclass
class Task:
    """One coding task: a prompt, a buggy starting project, and a verifier.

    ``behaviour`` describes the agent that will attempt it, not the task's
    difficulty alone. It is fixed before the run and identical in both
    experiment arms, which is what makes the comparison mean anything.

    ``support_files`` lets a task be a small project rather than one file. They
    are written before ``wrong_source``, and the verifier reads the workspace, so
    an agent has to go and look at them.
    """

    name: str
    prompt: str
    filename: str
    wrong_source: str
    correct_source: str
    verify: Callable[[Path], tuple[bool, str]]
    behaviour: AgentBehaviour | None = None
    setup: Callable[[Path], None] | None = None
    support_files: dict[str, str] = field(default_factory=dict)

    def prepare(self, workspace: Path) -> None:
        if self.setup is not None:
            self.setup(workspace)
        for path, source in self.support_files.items():
            target = workspace / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
        (workspace / self.filename).write_text(self.wrong_source, encoding="utf-8")


@dataclass
class RunResult:
    """What one harness run produced."""

    task: str
    arm: str
    success: bool
    steps: int
    cost: float
    input_tokens: int
    output_tokens: int
    adaptations: int
    outcome: str
    episode: Episode
    transcript: list[str] = field(default_factory=list)
    #: True when the run stopped because the provider's call budget ran out
    #: rather than because the task finished or the model gave up.
    truncated: bool = False

    def to_metrics(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "arm": self.arm,
            "success": self.success,
            "steps": self.steps,
            "cost": self.cost,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tokens": self.input_tokens + self.output_tokens,
            "adaptations": self.adaptations,
            "truncated": self.truncated,
        }


#: The tools the agent may call, in OpenAI function-calling shape.
#:
#: This is the harness's declaration of its own tools, not a provider's wire
#: format. Each provider translates it: an OpenAI-compatible API takes this as
#: written, and :class:`AnthropicProvider` rewrites ``parameters`` to
#: ``input_schema``. Keeping the declaration here and the translation in the
#: provider is what lets the same harness drive either without either knowing
#: about the other.
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write source into a file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run the task's tests and return the output.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


class CodingHarness:
    """Runs one task, letting Microloop adapt the runtime as it goes."""

    def __init__(
        self,
        *,
        task: Task,
        provider: Provider,
        session: RuntimeSession,
        arm: str = "adaptive",
        max_steps: int = 40,
        workspace: str | Path | None = None,
    ) -> None:
        self.task = task
        self.provider = provider
        self.session = session
        self.arm = arm
        self.max_steps = max_steps
        self.workspace = (
            Path(workspace) if workspace is not None else Path(tempfile.mkdtemp())
        )
        self.adapter = session.adapter
        self.task.prepare(self.workspace)
        self._verifications = 0
        self._success = False

    # -- tools ----------------------------------------------------------------
    def _dispatch(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool | None]:
        """Run one tool; return ``(observation, passed)``.

        ``passed`` is ``True``/``False`` for a verification tool and ``None`` for
        any other tool.
        """
        if name == "write_file":
            path = self.workspace / str(arguments["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(arguments.get("content", "")), encoding="utf-8")
            return f"wrote {arguments['path']}", None
        if name == "read_file":
            path = self.workspace / str(arguments["path"])
            if not path.exists():
                return f"{arguments['path']}: no such file", None
            return path.read_text(encoding="utf-8"), None
        if name == "run_tests":
            passed, output = self.task.verify(self.workspace)
            return output, passed
        return f"unknown tool {name}", None

    # -- loop -----------------------------------------------------------------
    def run(self) -> RunResult:
        adapter = self.adapter
        adapter.record_segment("task", self.task.prompt)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self.task.prompt},
        ]
        step = 0
        transcript: list[str] = []
        success = False
        truncated = False

        while step < self.max_steps:
            step += 1
            call_started = time.perf_counter()
            try:
                reply: ModelReply = self.provider.complete(
                    model=adapter.model, messages=messages, tools=TOOL_SCHEMAS
                )
            except BudgetExhausted as error:
                # Out of allowance, not out of ideas. Record it as an unfinished
                # run rather than a failed one: "we stopped paying" and "the
                # model gave up" are different facts and the report must not
                # conflate them.
                truncated = True
                transcript.append(f"{step:>3}  budget   {error}")
                break
            adapter.add_usage(
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                cost=reply.cost,
                # Wall time is measured around the model call, so the number in
                # the dataset is the time the run actually spent thinking rather
                # than the time the harness spent writing files.
                elapsed_seconds=time.perf_counter() - call_started,
            )
            if reply.text:
                adapter.record_segment("note", reply.text, step=step)

            passed: bool | None = None
            observation = reply.text or "no output"
            actions: list[str] = []
            results: list[tuple[Any, str]] = []
            for call in reply.tool_calls:
                adapter.add_tool_call()
                actions.append(f"{call.name} {call.arguments.get('path', '')}".strip())
                result, passed_here = self._dispatch(call.name, call.arguments)
                observation = result
                results.append((call, result))
                if passed_here is not None:
                    passed = passed_here
                    self._verifications += 1
                adapter.record_segment("tool", result, step=step)
            action = ", ".join(actions) or "think"

            metrics: dict[str, float] = {"exit_code": 1.0 if passed is False else 0.0}
            metadata: dict[str, str] = {}
            if passed is not None:
                metrics["failures"] = 0.0 if passed else 1.0
                metadata.update(
                    {
                        "verifier": "harness-tests",
                        "verification_id": f"{self.task.name}-{self._verifications}",
                    }
                )
                if not passed:
                    metadata["error"] = observation.splitlines()[0][:200]
                adapter.record_segment(
                    "verification", observation, step=step, passed=passed
                )

            decision = self.session.observe(
                action=action,
                observation=observation,
                metrics=metrics,
                metadata=metadata or None,
                step=step,
            )
            transcript.append(f"{step:>3}  {decision.status:<10} {action}")

            # The model has to see what its tools returned, or it cannot react
            # to a failing test. This is the difference between a loop and a
            # demonstration, and the offline agent depends on it too. The
            # provider's own ids travel with the result so the next turn can be
            # rebuilt in the API's wire format without guessing.
            assistant: dict[str, Any] = {
                "role": "assistant",
                "content": reply.text
                or ", ".join(f"{c.name}({c.arguments})" for c in reply.tool_calls),
            }
            if reply.raw_tool_calls:
                assistant["tool_calls"] = list(reply.raw_tool_calls)
            messages.append(assistant)
            for call, result in results:
                entry: dict[str, Any] = {
                    "role": "tool",
                    "name": call.name,
                    "content": result,
                }
                if call.identifier:
                    entry["identifier"] = call.identifier
                messages.append(entry)

            # The adapter may have queued a replan message; inject it so the next
            # model turn sees the new instruction. This is the real replan.
            replan = adapter.take_replan()
            if replan is not None:
                messages.append({"role": "user", "content": replan})

            if reply.done and not reply.tool_calls:
                break
            if passed:
                success = True
                break

        self._success = success
        if truncated:
            outcome = "budget_exhausted"
        else:
            outcome = "completed" if success else "failed"
        self.session.finish(outcome)
        usage = adapter.usage
        return RunResult(
            task=self.task.name,
            arm=self.arm,
            success=success,
            steps=step,
            cost=usage.cost or 0.0,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            adaptations=len(self.session.episode.adaptations),
            outcome=self.session.episode.outcome or outcome,
            truncated=truncated,
            episode=self.session.episode,
            transcript=transcript,
        )
