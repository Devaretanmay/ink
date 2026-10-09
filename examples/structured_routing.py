"""Reference Example A: Structured DecisionSite (`workflow.route`).

Demonstrates:
- High-volume bounded deterministic routing gate.
- ExactEngine compilation.
- Verified outcome accumulation.
- 0ms local Fast Path dispatch with automated comparison traffic.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements


def remote_llm_route(state: dict) -> FallbackResult:
    """Simulates a remote LLM routing complex workflows."""
    tier = state["tier"]
    amount = state["amount"]
    flagged = state["flagged"]

    if flagged or amount > 10000:
        choice = "manual_review"
    elif tier in ("enterprise", "premium") or amount < 500:
        choice = "auto_approve"
    else:
        choice = "standard_queue"

    # Report simulated frontier model usage metrics
    return FallbackResult(
        choice=choice,
        model_calls=1,
        input_tokens=420,
        output_tokens=15,
        cost=0.003,
        provider="anthropic",
        model="claude-3-5-sonnet",
    )


def workflow_verifier(state: dict, choice: str) -> Outcome:
    """Downstream verification from business workflow outcome."""
    expected = remote_llm_route(state).choice
    success = choice == expected
    return Outcome(
        quality=1.0 if success else 0.0,
        verifier="ledger_reconciliation",
        verifier_version="v2",
        evidence={"reconciled": success, "amount": state["amount"]},
    )


def main():
    print("=" * 70)
    print("  Ink Reference Example A: Structured Decision Routing")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "structured.db")
        ink = Ink(db_path)

        # Explicit bounded DecisionSite
        site = DecisionSite(
            name="workflow.route",
            description="Transaction routing and risk approval boundary",
            state_schema={"tier": "string", "amount": "number", "flagged": "boolean"},
            choices=("auto_approve", "standard_queue", "manual_review"),
        )

        patterns = [
            {"tier": "enterprise", "amount": 250.0, "flagged": False},
            {"tier": "standard", "amount": 1500.0, "flagged": False},
            {"tier": "enterprise", "amount": 15000.0, "flagged": False},
            {"tier": "free", "amount": 50.0, "flagged": False},
        ]

        print("\n1. Ingesting observed traffic and verifying business outcomes...")
        for i in range(100):
            state = patterns[i % len(patterns)]
            res = ink.decide(
                site=site,
                state=state,
                task_id=f"tx-{i}",
                fallback=lambda: remote_llm_route(state),
            )
            # Record verified outcome
            ink.record_outcome(
                res.decision_id,
                workflow_verifier(state, res.choice),
            )

        # Compile and promote
        print("2. Compiling ExactEngine candidate and calibrating in shadow mode...")
        ink.compile(site, engine="exact")
        req = PromotionRequirements(
            min_samples=8,
            min_quality=0.5,
            min_confidence=0.35,
            max_degradation=0.8,
            min_region_samples=3,
            evaluation_window=50,
        )
        ink.calibrate(site, verifier=workflow_verifier, requirements=req)

        print("3. Evaluating shadow traffic against production ground truth...")
        for i in range(30):
            state = patterns[i % len(patterns)]
            res = ink.decide(
                site=site,
                state=state,
                task_id=f"shadow-tx-{i}",
                fallback=lambda: remote_llm_route(state),
            )
            ink.record_outcome(
                res.decision_id,
                workflow_verifier(state, res.choice),
            )

        ink.evaluate(site, verifier=workflow_verifier)
        status = ink.status(site)
        print(f"   Site status: {status['state']} (Fast Path Revision: {status['active_revision']})")

        print("\n4. Serving live production traffic via local Fast Path:")
        for state in patterns:
            res = ink.decide(
                site=site,
                state=state,
                fallback=lambda: remote_llm_route(state),
            )
            print(f"   [{res.source.upper()}] State {state} -> Choice: {res.choice}")

        print("\n" + "=" * 70)
        print("  Example A completed successfully.")
        print("=" * 70)


if __name__ == "__main__":
    main()
