"""Reference Example C: Agent Graph Supervisor (`agent.supervisor`).

Demonstrates:
- Multi-agent orchestration delegation boundary.
- Routing tasks between specialized agents or concluding execution.
- Downstream task execution success recorded as verifier outcome evidence.
- Converting repetitive supervisor decisions into local Fast Paths.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements


def frontier_agent_supervisor(state: dict) -> FallbackResult:
    """Simulates an LLM supervisor deciding next step in an agent workflow."""
    task_type = state["task_type"]
    steps_completed = state["steps_completed"]

    if steps_completed >= 3 or task_type == "summary":
        choice = "finish"
    elif task_type in ("documentation", "fact_check", "search"):
        choice = "delegate_research"
    else:
        choice = "delegate_coder"

    return FallbackResult(
        choice=choice,
        model_calls=1,
        input_tokens=850,
        output_tokens=12,
        cost=0.006,
        provider="anthropic",
        model="claude-3-5-sonnet",
    )


def agent_execution_verifier(state: dict, choice: str) -> Outcome:
    """Verifies that the delegated agent successfully advanced or finished the task."""
    expected = frontier_agent_supervisor(state).choice
    success = choice == expected
    return Outcome(
        quality=1.0 if success else 0.0,
        verifier="subagent_task_evaluator",
        verifier_version="v1",
        evidence={"task_type": state["task_type"], "success": success},
    )


def main():
    print("=" * 70)
    print("  Ink Reference Example C: Agent Graph Supervisor Routing")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "supervisor.db")
        ink = Ink(db_path)

        site = DecisionSite(
            name="agent.supervisor",
            description="Multi-agent graph supervisor delegation boundary",
            state_schema={"task_type": "string", "steps_completed": "integer"},
            choices=("delegate_coder", "delegate_research", "finish"),
        )

        patterns = [
            {"task_type": "search", "steps_completed": 0},
            {"task_type": "code_refactor", "steps_completed": 1},
            {"task_type": "fact_check", "steps_completed": 1},
            {"task_type": "summary", "steps_completed": 2},
            {"task_type": "code_refactor", "steps_completed": 4},
        ]

        print("\n1. Observing agent execution traces and downstream completion signals...")
        for i in range(100):
            state = patterns[i % len(patterns)]
            res = ink.decide(
                site=site,
                state=state,
                task_id=f"agent-run-{i}",
                fallback=lambda: frontier_agent_supervisor(state),
            )
            ink.record_outcome(
                res.decision_id,
                agent_execution_verifier(state, res.choice),
            )

        print("2. Compiling local supervisor Fast Path candidate...")
        ink.compile(site, engine="exact")
        req = PromotionRequirements(
            min_samples=8,
            min_quality=0.5,
            min_confidence=0.35,
            max_degradation=0.8,
            min_region_samples=3,
            evaluation_window=50,
        )
        ink.calibrate(site, verifier=agent_execution_verifier, requirements=req)

        print("3. Observing shadow agent dispatches...")
        for i in range(30):
            state = patterns[i % len(patterns)]
            res = ink.decide(
                site=site,
                state=state,
                task_id=f"shadow-agent-{i}",
                fallback=lambda: frontier_agent_supervisor(state),
            )
            ink.record_outcome(
                res.decision_id,
                agent_execution_verifier(state, res.choice),
            )

        ink.evaluate(site, verifier=agent_execution_verifier)
        status = ink.status(site)
        print(f"   Site status: {status['state']} (Fast Path Revision: {status['active_revision']})")

        print("\n4. Serving live supervisor delegation requests:")
        for state in patterns:
            res = ink.decide(
                site=site,
                state=state,
                fallback=lambda: frontier_agent_supervisor(state),
            )
            print(f"   [{res.source.upper()}] State {state} -> Action: {res.choice}")

        print("\n" + "=" * 70)
        print("  Example C completed successfully.")
        print("=" * 70)


if __name__ == "__main__":
    main()
