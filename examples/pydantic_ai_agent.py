"""Framework Integration B: PydanticAI Decision Wrapper.

Demonstrates how to accelerate PydanticAI agent structured decisions by wrapping
tool selections or classification boundaries with Ink Fast Paths.

Allows an agent tool or routing decision to be bypassed when qualified,
saving LLM inference latency while preserving structured output guarantees.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from collections.abc import Callable

from ink import DecisionSite, Ink, Outcome, PromotionRequirements


def ink_agent_tool(
    site: DecisionSite,
    tool_implementation: Callable[..., Any],
    *,
    client: Ink | None = None,
) -> Callable[..., Any]:
    """Wraps an agent tool or classification handler with Ink Fast Path acceleration."""
    ink = client or Ink()

    def wrapped_tool(*args, **kwargs):
        # Extract schema attributes from keyword arguments
        state = {k: kwargs[k] for k in site.state_schema if k in kwargs}
        if not state and args and isinstance(args[0], dict):
            state = {k: args[0][k] for k in site.state_schema if k in args[0]}

        res = ink.decide(
            site=site,
            state=state,
            fallback=lambda: tool_implementation(*args, **kwargs),
        )
        # Store metadata on wrapper for downstream recording if needed
        wrapped_tool.last_decision = res
        return res.choice

    wrapped_tool.last_decision = None
    return wrapped_tool


def main():
    print("=" * 70)
    print("  Ink Framework Adapter: PydanticAI Agent Tool Acceleration")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "pydantic_ai.db")
        ink = Ink(db_path)

        site = DecisionSite(
            name="pydantic_ai.classify_intent",
            description="Agent intent classifier tool",
            state_schema={"query_type": "string", "authenticated": "boolean"},
            choices=("read_data", "write_data", "request_login"),
        )

        def slow_agent_classifier(query_type: str, authenticated: bool) -> str:
            """Simulates an LLM agent structured output classification."""
            if not authenticated:
                return "request_login"
            if query_type in ("analytics", "metrics", "profile"):
                return "read_data"
            return "write_data"

        tool = ink_agent_tool(site, slow_agent_classifier, client=ink)

        print("\n1. Observing PydanticAI tool invocations and user validation signals...")
        inputs = [
            {"query_type": "profile", "authenticated": False},
            {"query_type": "analytics", "authenticated": True},
            {"query_type": "update_email", "authenticated": True},
        ]

        for i in range(60):
            inp = inputs[i % len(inputs)]
            choice = tool(**inp)
            ink.record_outcome(
                tool.last_decision.decision_id,
                Outcome(
                    quality=1.0,
                    verifier="pydantic_schema_verifier",
                    verifier_version="v1",
                    evidence={"choice": choice},
                ),
            )

        print("2. Qualifying agent tool candidate into ACTIVE Fast Path...")
        ink.compile(site, engine="exact")
        req = PromotionRequirements(
            min_samples=6,
            min_quality=0.5,
            min_confidence=0.35,
            max_degradation=0.8,
            min_region_samples=3,
            evaluation_window=50,
        )
        ink.calibrate(
            site,
            verifier=lambda st, c: Outcome(1.0, "ver", "1", {}),
            requirements=req,
        )

        for i in range(20):
            inp = inputs[i % len(inputs)]
            tool(**inp)
            ink.record_outcome(
                tool.last_decision.decision_id,
                Outcome(1.0, "ver", "1", {}),
            )

        ink.evaluate(site, verifier=lambda st, c: Outcome(1.0, "ver", "1", {}))
        print(f"   Tool Fast Path status: {ink.status(site)['state']}")

        print("\n3. Invoking agent tool via Fast Path:")
        for inp in inputs:
            action = tool(**inp)
            source = tool.last_decision.source.upper()
            print(f"   [{source}] Input: {inp} -> Action: '{action}'")

        print("\n" + "=" * 70)
        print("  PydanticAI integration demonstrated successfully.")
        print("=" * 70)


if __name__ == "__main__":
    main()
