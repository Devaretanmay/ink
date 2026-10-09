"""Framework Integration A: LangGraph Conditional Edge Adapter.

Demonstrates how to accelerate LangGraph state graph routing by wrapping
conditional edge functions with Ink Fast Paths.

In LangGraph:
    workflow.add_conditional_edges("supervisor", ink_edge(site, router_fn), {...})

When the decision is repeated and verified, Ink bypasses the supervisor LLM call
entirely, reducing node transition latency to sub-millisecond.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from collections.abc import Callable

from ink import DecisionSite, Ink, Outcome, PromotionRequirements


def ink_conditional_edge(
    site: DecisionSite,
    fallback_router: Callable[[dict[str, Any]], str],
    *,
    client: Ink | None = None,
    state_extractor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> Callable[[dict[str, Any]], str]:
    """Wraps a LangGraph conditional edge routing function with Ink."""
    ink = client or Ink()

    def edge_router(state: dict[str, Any]) -> str:
        # Extract decision fields matching the site schema from LangGraph state dict
        decision_state = state_extractor(state) if state_extractor else state
        # Filter down to declared schema keys if necessary
        filtered_state = {k: decision_state[k] for k in site.state_schema if k in decision_state}

        result = ink.decide(
            site=site,
            state=filtered_state,
            fallback=lambda: fallback_router(state),
        )
        # Attach decision metadata to graph state for subsequent outcome recording
        if isinstance(state, dict):
            state["_ink_last_decision_id"] = result.decision_id
            state["_ink_source"] = result.source
        return result.choice

    return edge_router


def main():
    print("=" * 70)
    print("  Ink Framework Adapter: LangGraph Conditional Edges")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "langgraph.db")
        ink = Ink(db_path)

        site = DecisionSite(
            name="langgraph.agent_router",
            description="LangGraph conditional edge router",
            state_schema={"step": "string", "error_count": "integer"},
            choices=("retry_tool", "escalate_human", "continue_workflow"),
        )

        def llm_supervisor_router(graph_state: dict) -> str:
            """Simulated expensive supervisor LLM decision."""
            errors = graph_state.get("error_count", 0)
            if errors >= 2:
                return "escalate_human"
            elif errors > 0:
                return "retry_tool"
            return "continue_workflow"

        # Wrap conditional edge function with Ink
        wrapped_edge = ink_conditional_edge(site, llm_supervisor_router, client=ink)

        print("\n1. Simulating LangGraph workflow executions...")
        for i in range(60):
            # Simulated LangGraph state object passed between nodes
            graph_state = {
                "step": "execute_tool",
                "error_count": i % 3,
                "messages": [{"role": "user", "content": "Fetch report"}],
            }
            next_node = wrapped_edge(graph_state)
            # Downstream node execution completes and records outcome
            ink.record_outcome(
                graph_state["_ink_last_decision_id"],
                Outcome(
                    quality=1.0,
                    verifier="node_execution_verifier",
                    verifier_version="v1",
                    evidence={"next_node": next_node},
                ),
            )

        print("2. Qualifying candidate Fast Path from LangGraph state history...")
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
            graph_state = {"step": "execute_tool", "error_count": i % 3}
            wrapped_edge(graph_state)
            ink.record_outcome(
                graph_state["_ink_last_decision_id"],
                Outcome(1.0, "ver", "1", {}),
            )

        ink.evaluate(site, verifier=lambda st, c: Outcome(1.0, "ver", "1", {}))
        print(f"   Router status: {ink.status(site)['state']}")

        print("\n3. Executing LangGraph conditional edges via Ink Fast Path:")
        for errors in (0, 1, 2):
            test_state = {"step": "execute_tool", "error_count": errors}
            next_node = wrapped_edge(test_state)
            src = test_state["_ink_source"].upper()
            print(f"   [{src}] Error count: {errors} -> Route to: '{next_node}'")

        print("\n" + "=" * 70)
        print("  LangGraph integration demonstrated successfully.")
        print("=" * 70)


if __name__ == "__main__":
    main()
