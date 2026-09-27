"""Model providers for the coding harness.

The harness owns the tool loop; a provider only turns a transcript into the next
model reply. Two implementations ship:

* :class:`SimulatedCodingProvider` -- a deterministic *model of an agent*, used
  for offline dry runs and tests. It reads verification output and user
  instructions, exactly as a real model would, and it is deliberately blind to
  whether Microloop is running.
* :class:`AnthropicProvider` -- the real one. It imports the Anthropic SDK lazily
  so the package works without it, and never leaks a model name into core.

A note on the simulated provider, because it is the difference between an
experiment and a demonstration. An earlier version applied the known fix only
once it spotted a Microloop-shaped message in the transcript, which made the
static arm incapable of success and the comparison meaningless. The rule now is
that behaviour is a property of the *task*, fixed before the run starts, and
identical in both arms. Microloop can only influence an agent the same way a
person reading the terminal could: by changing the model's tier or by putting a
new instruction in its context. Neither is a guarantee, which is why the
adaptive arm is allowed to fail.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "AgentBehaviour",
    "AnthropicProvider",
    "Ceiling",
    "ModelReply",
    "Provider",
    "SimulatedCodingProvider",
    "ToolCall",
]


@dataclass
class ToolCall:
    """One tool the model asked the harness to run."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelReply:
    """What a model returned for one turn."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    done: bool = False


@runtime_checkable
class Provider(Protocol):
    """Turns a transcript into the next model reply."""

    def complete(
        self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelReply:
        """Return the model's next reply."""


class Ceiling:
    """The best approach an agent is capable of reaching on a given task.

    ``Correct`` agents can find the fix. ``Plausible`` agents genuinely change
    approach and still land on a wrong answer, which is the case that makes a
    replan look like it worked. ``Hopeless`` agents never move off their first
    strategy, so no adaptation can help them.
    """

    Correct = "correct"
    Plausible = "plausible"
    Hopeless = "hopeless"


#: How many failed verification runs one user instruction is worth. A replan is
#: an explicit instruction to change tactics, so it should move a stuck agent
#: more than re-running the same test does -- but it is a nudge, not a command,
#: which is why the value is finite.
NUDGE_WEIGHT = 3

#: What each tier can do that the tier below it cannot. Escalation is a real
#: lever, but a partial one: a stronger model reasons better, it does not know
#: the answer.
_TIER_STEP = {"fast": 0, "balanced": 1, "strong": 2}


@dataclass
class AgentBehaviour:
    """How one agent behaves when it gets stuck, fixed before the run starts.

    ``patience`` is how much pressure, in units, the agent tolerates before
    adopting a different approach. ``ceiling`` is the best approach it can reach.
    ``settle`` is how much evidence it wants before it will commit to an
    approach -- and, therefore, how easily an instruction can knock it off one
    it has barely tried. None of this depends on the runtime, so the static and
    adaptive arms face an identical opponent.
    """

    patience: int
    ceiling: str = Ceiling.Correct
    distractors: tuple[str, ...] = ()
    #: Evidence the agent wants before it treats an instruction as informed
    #: rather than as an interruption.
    settle: int = 1
    #: Extra pressure a nudge needs before it counts, modelling an agent that
    #: ignores instructions it has heard too often.
    stubbornness: int = 0


class SimulatedCodingProvider:
    """A deterministic offline model of a coding agent.

    The agent reads the transcript the way a real model does: it counts the
    verification failures it has been shown and the extra instructions it has
    been given, and it advances through its own ladder of approaches when the
    accumulated pressure passes its patience. It never inspects the transcript
    for evidence that Microloop is attached.
    """

    def __init__(
        self,
        *,
        filename: str,
        wrong_source: str,
        correct_source: str,
        behaviour: AgentBehaviour | None = None,
        tier_by_model: Mapping[str, str] | None = None,
    ) -> None:
        self.filename = filename
        self.wrong_source = wrong_source
        self.correct_source = correct_source
        self.behaviour = behaviour or AgentBehaviour(patience=1)
        self.tier_by_model = dict(tier_by_model or {})
        self.turn = 0
        self.rung = 0
        #: Pressure earned since the current approach was adopted. Local on
        #: purpose: an agent that is told to change strategy forgets what it
        #: learned on the approach it just abandoned.
        self.local = 0
        self.failures = 0
        self.nudges = 0
        self.interruptions = 0
        self._seen_failures = 0
        self._seen_nudges = 0
        self.initial_model: str | None = None

    # -- reading the transcript -----------------------------------------------
    def _tier(self, model: str) -> str:
        return self.tier_by_model.get(model, "fast")

    def _ladder(self) -> tuple[str, ...]:
        """The approaches available, worst first.

        The ladder does not depend on the tier: every tier *could* in principle
        reach the fix. What a stronger model buys is patience, not access, which
        is what makes escalation a real but partial lever.
        """
        approaches = [self.wrong_source, *self.behaviour.distractors]
        if self.behaviour.ceiling == Ceiling.Correct:
            approaches.append(self.correct_source)
        return tuple(approaches)

    def _read(self, messages: list[dict[str, Any]]) -> None:
        """Count verification failures and extra instructions in the transcript."""
        self.failures = 0
        instructions = 0
        seen_prompt = False
        for message in messages:
            role = message.get("role")
            content = str(message.get("content") or "")
            if role == "tool":
                if _is_failure(content):
                    self.failures += 1
            elif role == "user":
                # The opening prompt is the task, not an instruction. Anything the
                # user says after it is: a replan, a clarification, a correction.
                # Identified by position among user turns rather than by index,
                # so a leading system message cannot shift the count.
                if seen_prompt:
                    instructions += 1
                else:
                    seen_prompt = True
        self.nudges = instructions

    def _threshold(self, tier: str) -> int:
        """Pressure needed to adopt the next approach, at this tier."""
        return max(1, self.behaviour.patience - _TIER_STEP.get(tier, 0))

    def _respond(self, tier: str) -> None:
        """React to what arrived since the last turn.

        Two things happen, and the second is why an adaptive runtime can make
        things worse. An instruction adds pressure, and pressure is what unlocks
        a better approach. But an instruction that lands before the agent has
        gathered evidence on its current approach reads as an interruption, and
        the agent abandons that approach, throwing away the ground it had made.
        A nudge at the wrong moment costs steps and can cost the run.
        """
        if self.behaviour.ceiling == Ceiling.Hopeless:
            return
        threshold = self._threshold(tier)
        new_failures = self.failures - self._seen_failures
        new_nudges = self.nudges - self._seen_nudges
        if new_nudges:
            weight = NUDGE_WEIGHT + _TIER_STEP.get(tier, 0) - self.behaviour.stubbornness
            for _ in range(new_nudges):
                if self.rung > 0 and self.local < self.behaviour.settle:
                    self.rung -= 1
                    self.interruptions += 1
                    self.local = 0
                else:
                    self.local += max(1, weight)
        self.local += max(0, new_failures)
        ladder = self._ladder()
        while self.local >= threshold and self.rung < len(ladder) - 1:
            self.local -= threshold
            self.rung += 1
        self._seen_failures = self.failures
        self._seen_nudges = self.nudges

    def _source(self) -> str:
        if self.behaviour.ceiling == Ceiling.Hopeless:
            return self.wrong_source
        return self._ladder()[self.rung]

    # -- provider protocol -----------------------------------------------------
    def complete(
        self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelReply:
        self.turn += 1
        if self.initial_model is None:
            self.initial_model = model
        self._read(messages)
        tier = self._tier(model)
        self._respond(tier)

        # Alternate an edit with a verification run so the trajectory carries
        # both, which is what the progress detectors need to see.
        if self.turn % 2 == 1:
            return ModelReply(
                text=f"Editing {self.filename}.",
                tool_calls=[
                    ToolCall(
                        "write_file",
                        {"path": self.filename, "content": self._source()},
                    )
                ],
                input_tokens=120,
                output_tokens=40,
            )
        return ModelReply(
            text="Running the tests.",
            tool_calls=[ToolCall("run_tests", {})],
            input_tokens=80,
            output_tokens=20,
        )


def _is_failure(text: str) -> bool:
    """Whether a verification result reads as a failure.

    This is a judgement about the text a model is shown, not a peek at a
    structured field. A real model reads ``1 failed: ...`` and reacts; so does
    this one.
    """
    lowered = text.strip().lower()
    if not lowered:
        return False
    return "failed" in lowered or "error" in lowered or "assertion" in lowered


class AnthropicProvider:
    """A real provider backed by the Anthropic SDK.

    Model ids come from the adapter's tier map, so this class never hardcodes a
    name. The SDK is imported lazily: constructing the provider without it, or
    running the offline harness, never touches the network.
    """

    def __init__(self, *, max_tokens: int = 1024, temperature: float = 0.0) -> None:
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client: Any = None
        self._pending_tool_ids: list[str] = []

    def _sdk(self) -> Any:
        if self._client is None:
            try:
                import anthropic  # type: ignore
            except ImportError as error:  # pragma: no cover - environment dependent
                raise RuntimeError(
                    "the Anthropic provider needs the 'anthropic' package; "
                    "install it with `pip install anthropic`"
                ) from error
            self._client = anthropic.Anthropic()
        return self._client

    def _wire(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert the harness transcript into Anthropic message objects.

        Tool results are returned as ``tool_result`` blocks against the ids the
        previous response issued, which is the form the API expects; a plain
        user message would be accepted but drops the tool's identity, and the
        model is measurably worse at attributing a failure to the call that
        caused it.
        """
        pending = list(self._pending_tool_ids)
        self._pending_tool_ids = []
        wired: list[dict[str, Any]] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            role = message.get("role")
            if role == "tool":
                blocks = []
                while index < len(messages) and messages[index].get("role") == "tool":
                    block: dict[str, Any] = {
                        "type": "tool_result",
                        "content": str(messages[index].get("content") or ""),
                    }
                    if pending:
                        block["tool_use_id"] = pending.pop(0)
                    blocks.append(block)
                    index += 1
                wired.append({"role": "user", "content": blocks})
                continue
            if role in ("user", "assistant"):
                wired.append({"role": role, "content": str(message.get("content") or "")})
            index += 1
        return wired

    def complete(
        self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelReply:
        client = self._sdk()
        response = client.messages.create(
            model=model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=next(
                (str(m["content"]) for m in messages if m.get("role") == "system"), ""
            ),
            messages=self._wire(messages),
            tools=tools or None,
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        calls: list[ToolCall] = []
        self._pending_tool_ids = []
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                identifier = getattr(block, "id", None)
                if identifier:
                    self._pending_tool_ids.append(identifier)
                calls.append(ToolCall(name=block.name, arguments=dict(block.input or {})))
        usage = getattr(response, "usage", None)
        return ModelReply(
            text=text,
            tool_calls=calls,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cost=_cost_of(response, model),
            done=not calls,
        )


def _cost_of(response: Any, model: str) -> float:
    """Cost of a response, when the SDK exposes it.

    Microloop never prices a model, so this reports whatever the host measured
    and returns ``0.0`` when the host measured nothing.
    """
    get_usage = getattr(response, "get_usage", None)
    if not callable(get_usage):
        return 0.0
    try:
        return float(get_usage().calculate_cost() or 0.0)
    except Exception:  # noqa: BLE001 - pricing is optional, never fatal
        return 0.0


def _coerce_reply(payload: Mapping[str, Any]) -> ModelReply:
    """Build a reply from a plain mapping (used by tests and traces)."""
    return ModelReply(
        text=str(payload.get("text", "")),
        tool_calls=[
            ToolCall(str(call["name"]), dict(call.get("arguments") or {}))
            for call in payload.get("tool_calls", [])
        ],
        input_tokens=int(payload.get("input_tokens", 0)),
        output_tokens=int(payload.get("output_tokens", 0)),
        cost=float(payload.get("cost", 0.0)),
        done=bool(payload.get("done", False)),
    )
