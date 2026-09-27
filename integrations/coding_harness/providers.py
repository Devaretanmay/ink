"""Model providers for the coding harness.

The harness owns the tool loop; a provider only turns a transcript into the next
model reply. Two implementations ship:

* :class:`SimulatedCodingProvider` -- a deterministic *model of an agent*, used
  for offline dry runs and tests. It reads verification output and user
  instructions, exactly as a real model would, and it is deliberately blind to
  whether Microloop is running.
* :class:`AnthropicProvider` -- real, via the Anthropic SDK.
* :class:`GroqProvider` -- real, via Groq's OpenAI-compatible API. Uses the
  standard library, so it adds no dependency and the harness stays installable
  with nothing but the runtime.

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

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "AgentBehaviour",
    "AnthropicProvider",
    "BudgetExhausted",
    "CallBudget",
    "Ceiling",
    "GroqProvider",
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
    #: The id the API assigned to this call, when it assigned one. Providers need
    #: it to send the result back, and the harness carries it in the transcript
    #: so that wiring a turn needs no memory of the turn before it.
    identifier: str | None = None


@dataclass
class ModelReply:
    """What a model returned for one turn."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    done: bool = False
    #: The provider's own representation of the calls it requested, passed
    #: through into the transcript so the next turn can be rebuilt exactly. The
    #: harness stores it without interpreting it.
    raw_tool_calls: list[dict[str, Any]] = field(default_factory=list)


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
        response issued, which is the form the API expects; a plain user message
        would be accepted but drops the tool's identity, and the model is
        measurably worse at attributing a failure to the call that caused it.

        Stateless, like the Groq provider: the ids come from the transcript
        rather than from a queue of the most recent turn, so every turn in a
        long run keeps its tools.
        """
        wired: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role == "tool":
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "content": str(message.get("content") or ""),
                }
                identifier = message.get("identifier")
                if identifier:
                    block["tool_use_id"] = identifier
                wired.append({"role": "user", "content": [block]})
            elif role == "assistant":
                entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": str(message.get("content") or ""),
                }
                raw = message.get("tool_calls") or []
                if raw:
                    entry["content"] = [entry["content"], *raw]
                wired.append(entry)
            elif role in ("system", "user"):
                wired.append({"role": role, "content": str(message.get("content") or "")})
        return wired

    @staticmethod
    def _tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Rewrite OpenAI function declarations into Anthropic tool declarations.

        The harness declares its tools once, in the OpenAI shape, because that is
        the shape most OpenAI-compatible APIs take. Anthropic wants the schema
        hoisted to ``input_schema``, which is a rename and nothing more.
        """
        declared = []
        for tool in tools:
            function = tool.get("function") or tool
            declared.append(
                {
                    "name": function.get("name"),
                    "description": function.get("description", ""),
                    "input_schema": function.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
        return declared

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
            tools=self._tools(tools) or None,
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        calls: list[ToolCall] = []
        raw: list[dict[str, Any]] = []
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                identifier = getattr(block, "id", None)
                calls.append(
                    ToolCall(
                        name=block.name,
                        arguments=dict(block.input or {}),
                        identifier=identifier,
                    )
                )
                if identifier:
                    raw.append(
                        {
                            "type": "tool_use",
                            "id": identifier,
                            "name": block.name,
                            "input": dict(block.input or {}),
                        }
                    )
        usage = getattr(response, "usage", None)
        return ModelReply(
            text=text,
            tool_calls=calls,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cost=_cost_of(response, model),
            done=not calls,
            raw_tool_calls=raw,
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


#: Identifies the client to the API edge. Set explicitly because the default
#: urllib agent string is refused by Groq's edge with Cloudflare 1010 ("browser
#: signature banned"), and an API client that cannot name itself is
#: indistinguishable from a scraper.
USER_AGENT = "microloop-experiment/0.4 (agent runtime evaluation harness)"


class BudgetExhausted(RuntimeError):
    """Raised when a metered provider is out of allowance.

    Raised rather than logged, because the only safe response to an exhausted
    budget is to stop. A provider that returned a partial answer instead would
    make the run look like a model failure, and the run would keep spending.
    """


def _is_tool_use_failure(body: str) -> bool:
    """Whether a 400 is the model's invention rather than a bad request.

    Distinguishing these matters: one is fixed by asking again, the other means
    the request itself is wrong and retrying only wastes the budget.
    """
    return "tool_use_failed" in body or "Tool call validation failed" in body


def _retry_after(body: str, default: float) -> float:
    """How long the server asked us to wait, if it said.

    Groq reports the wait in the message ("Please try again in 3.5625s"), and
    honouring it is faster than any backoff we would invent. Parsed leniently:
    a body that does not match falls back to the caller's delay.
    """
    match = re.search(r"try again in\s*([0-9.]+)\s*s", body)
    return float(match.group(1)) if match else default


class CallBudget:
    """A ceiling on inference calls shared across a whole run.

    A per-provider limit is not a run limit. The experiment builds a fresh
    provider for every task, so a per-instance cap multiplies by the number of
    tasks: ``--max-calls 400`` over 24 tasks would have meant about 9,600 calls
    rather than 400. The cap has to be owned by something that outlives the
    provider, and the run is the only thing that does.
    """

    def __init__(self, limit: int | None) -> None:
        self.limit = limit
        self.spent = 0

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(0, self.limit - self.spent)

    def charge(self) -> None:
        """Record one inference call, or refuse to start it."""
        if self.limit is not None and self.spent >= self.limit:
            raise BudgetExhausted(
                f"call budget of {self.limit} is spent; stopping rather than "
                f"starting a run that cannot finish"
            )
        self.spent += 1

    def can_afford(self, cost: int = 1) -> bool:
        return self.limit is None or self.spent + cost <= self.limit


class GroqProvider:
    """A real provider on Groq's OpenAI-compatible chat completions API.

    Standard library only, so the harness keeps working with nothing installed
    beyond the runtime. ``GROQ_API_KEY`` is read from the environment and never
    stored on the instance, and no key is ever written to the episode store.

    ``max_calls`` is a hard ceiling on inference calls for the lifetime of this
    provider. It exists because a metered API and an agent loop are a bad
    pairing without one: the loop decides how many turns to take, and nothing in
    it knows what a turn costs. Exceeding the budget raises
    :class:`BudgetExhausted` rather than returning a partial answer.

    Two details matter for a measurement to mean anything. Tool results are sent
    back as proper ``role="tool"`` messages carrying the ``tool_call_id`` the API
    issued, so the model can attribute a failure to the call that caused it. And
    the assistant turn that requested those tools is re-sent with its
    ``tool_calls`` intact, because an OpenAI-shaped conversation without it is
    not a conversation the model can continue.
    """

    #: Overridable so the provider can be pointed at a mock in tests.
    ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(
        self,
        *,
        max_tokens: int = 1024,
        base_url: str | None = None,
        timeout: float = 60.0,
        api_key: str | None = None,
        max_calls: int | None = None,
        budget: CallBudget | None = None,
        max_retries: int = 8,
        retry_delay: float = 4.0,
        max_retry_delay: float = 30.0,
    ) -> None:
        self.max_tokens = max_tokens
        #: A rate-limited request is retried rather than failed. Groq's free tier
        #: is 8,000 tokens per minute, and a single agent turn can be a few
        #: thousand, so a run crosses the limit routinely.
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.max_retry_delay = max_retry_delay
        self.retries = 0
        self.base_url = base_url or self.ENDPOINT
        self.timeout = timeout
        self._api_key = api_key
        #: Prefer ``budget``: a per-instance limit multiplies by the number of
        #: tasks, because the experiment builds one provider per task.
        self.max_calls = max_calls
        self.budget = budget or (CallBudget(max_calls) if max_calls is not None else None)
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def _key(self) -> str:
        key = self._api_key or os.environ.get("GROQ_API_KEY", "")
        if not key:
            raise RuntimeError(
                "the Groq provider needs GROQ_API_KEY set; Microloop never stores "
                "a provider credential"
            )
        return key

    def _wire(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Rebuild the wire format from a transcript that already carries the ids.

        Stateless on purpose. An earlier version queued only the most recent
        turn's ids, which silently stripped the tool calls and results of every
        turn before it -- so the longer the run, the more of the conversation the
        model stopped being able to see. The harness records what the provider
        returned, so reading it back is both total and testable.
        """
        wired: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role == "tool":
                entry: dict[str, Any] = {
                    "role": "tool",
                    "content": str(message.get("content") or ""),
                }
                identifier = message.get("identifier")
                if identifier:
                    entry["tool_call_id"] = identifier
                wired.append(entry)
            elif role == "assistant":
                entry = {"role": "assistant", "content": str(message.get("content") or "")}
                raw = message.get("tool_calls") or []
                if raw:
                    entry["tool_calls"] = list(raw)
                wired.append(entry)
            elif role in ("system", "user"):
                wired.append({"role": role, "content": str(message.get("content") or "")})
        return wired

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._key()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        delay = self.retry_delay
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", "replace")[:500]
                # Two classes of failure are recoverable and must not end a run.
                #
                # A 429 is the free tier's 8,000 tokens per minute, and the server
                # says how long to wait. Honouring it is faster than any backoff
                # we would invent.
                #
                # A 400 with code `tool_use_failed` is the *model* inventing a
                # tool, rejected at generation time. Our own request is valid;
                # the model's output is not, and re-sampling usually fixes it.
                # Without this, one hallucinated tool name ends the experiment.
                recoverable = error.code == 429 or _is_tool_use_failure(body)
                if recoverable and attempt < self.max_retries:
                    wait = _retry_after(body, delay)
                    self.retries += 1
                    time.sleep(wait)
                    delay = min(delay * 2, self.max_retry_delay)
                    continue
                self.calls += 1
                raise RuntimeError(
                    f"Groq returned HTTP {error.code}: {body}"
                ) from error
            except urllib.error.URLError as error:
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay = min(delay * 2, self.max_retry_delay)
                    continue
                raise RuntimeError(f"could not reach Groq: {error.reason}") from error
        raise RuntimeError("Groq request failed after retries")

    def complete(
        self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelReply:
        if self.budget is not None:
            self.budget.charge()
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._wire(messages),
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        self.requests.append(payload)
        data = self._post(payload)
        self.calls += 1

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        declared = {
            str((tool.get("function") or {}).get("name") or tool.get("name"))
            for tool in tools
        }
        calls: list[ToolCall] = []
        raw: list[dict[str, Any]] = []
        dropped: list[str] = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            name = str(function.get("name", ""))
            # A model will invent a tool name. Keeping the call in the
            # transcript poisons every later request: an OpenAI-compatible API
            # validates that the conversation only mentions tools it was given,
            # and rejects the whole run rather than the one bad call. So an
            # undeclared call is dropped here and reported to the model instead.
            if declared and name not in declared:
                dropped.append(name)
                continue
            arguments_raw = function.get("arguments")
            try:
                arguments = (
                    json.loads(arguments_raw)
                    if isinstance(arguments_raw, str)
                    else dict(arguments_raw or {})
                )
            except json.JSONDecodeError:
                arguments = {}
            identifier = call.get("id")
            calls.append(ToolCall(name=name, arguments=arguments, identifier=identifier))
            if identifier:
                raw.append({key: value for key, value in call.items() if value is not None})
        text = str(message.get("content") or "")
        if dropped:
            note = f"Those tools do not exist and were ignored: {', '.join(dropped)}."
            text = f"{text}\n{note}".strip()
        usage = data.get("usage") or {}
        return ModelReply(
            text=text,
            tool_calls=calls,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            # A turn that asked only for tools that do not exist has made no
            # progress, so it must not be allowed to end the run.
            done=not calls and not dropped,
            raw_tool_calls=raw,
        )


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
