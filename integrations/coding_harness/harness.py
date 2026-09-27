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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from microloop import Episode, RuntimeSession

from integrations.coding_harness.providers import AgentBehaviour, ModelReply, Provider

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
    """One coding task: a prompt, a buggy starting file, and a verifier.

    ``behaviour`` describes the agent that will attempt it, not the task's
    difficulty alone. It is fixed before the run and identical in both
    experiment arms, which is what makes the comparison mean anything.
    """

    name: str
    prompt: str
    filename: str
    wrong_source: str
    correct_source: str
    verify: Callable[[Path], tuple[bool, str]]
    behaviour: AgentBehaviour | None = None
    setup: Callable[[Path], None] | None = None

    def prepare(self, workspace: Path) -> None:
        if self.setup is not None:
            self.setup(workspace)
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
        }


TOOL_SCHEMAS = [
    {
        "name": "write_file",
        "description": "Write source into a file in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file from the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "run_tests",
        "description": "Run the task's tests and return the output.",
        "input_schema": {"type": "object", "properties": {}},
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

        while step < self.max_steps:
            step += 1
            reply: ModelReply = self.provider.complete(
                model=adapter.model, messages=messages, tools=TOOL_SCHEMAS
            )
            adapter.add_usage(
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                cost=reply.cost,
            )
            if reply.text:
                adapter.record_segment("note", reply.text, step=step)

            passed: bool | None = None
            observation = reply.text or "no output"
            actions: list[str] = []
            results: list[tuple[str, str]] = []
            for call in reply.tool_calls:
                adapter.add_tool_call()
                actions.append(f"{call.name} {call.arguments.get('path', '')}".strip())
                result, passed_here = self._dispatch(call.name, call.arguments)
                observation = result
                results.append((call.name, result))
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
            # demonstration, and the offline agent depends on it too.
            messages.append(
                {
                    "role": "assistant",
                    "content": reply.text
                    or ", ".join(f"{c.name}({c.arguments})" for c in reply.tool_calls),
                }
            )
            for name, result in results:
                messages.append({"role": "tool", "name": name, "content": result})

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
        self.session.finish("completed" if success else "failed")
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
            outcome=self.session.episode.outcome or "failed",
            episode=self.session.episode,
            transcript=transcript,
        )
