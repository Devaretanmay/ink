"""5-Minute Quickstart: Your First Ink Fast Path.

Shows how Ink observes repeated decisions, qualifies local behavior from
verified outcomes, and compiles an ultra-fast local Fast Path.

Run with:
    python examples/quickstart.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ink import DecisionSite, Ink, Outcome, PromotionRequirements


def simulate_expensive_frontier_model(state: dict) -> str:
    """Simulates a remote frontier model making a bounded workflow decision."""
    # Production systems call Claude / GPT-4 / Gemini here.
    if state["priority"] == "critical" or state["retries"] >= 3:
        return "escalate"
    elif state["retries"] > 0:
        return "retry"
    return "resolve"


def verifier(state: dict, choice: str) -> Outcome:
    """Independent ground-truth verifier evaluating decision quality."""
    expected = simulate_expensive_frontier_model(state)
    quality = 1.0 if choice == expected else 0.0
    return Outcome(
        quality=quality,
        verifier="workflow_verifier",
        verifier_version="v1",
        evidence={"expected": expected, "match": quality == 1.0},
    )


def main():
    print("=" * 65)
    print("  Ink Quickstart: Bounded Decisions -> Local Fast Paths")
    print("=" * 65)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "quickstart.db")
        ink = Ink(db_path)

        # 1. Define explicit bounded DecisionSite contract
        site = DecisionSite(
            name="workflow.next_action",
            description="Workflow engine retry and escalation gate",
            state_schema={"priority": "string", "retries": "integer"},
            choices=("retry", "escalate", "resolve"),
        )

        print("\n1. Observing production decisions and ground truth outcomes...")
        # Repeated production workload
        training_states = [
            {"priority": "low", "retries": 0},
            {"priority": "critical", "retries": 0},
        ]

        # Accumulate observed decisions & verified outcomes
        for i in range(60):
            state = training_states[i % len(training_states)]
            # Decide: Ink falls back to frontier model while observing
            res = ink.decide(
                site=site,
                state=state,
                task_id=f"obs-{i}",
                fallback=lambda: simulate_expensive_frontier_model(state),
            )
            # Record independent verified outcome
            ink.record_outcome(
                res.decision_id,
                Outcome(
                    quality=1.0,
                    verifier="workflow_verifier",
                    verifier_version="v1",
                    evidence={"success": True},
                ),
            )

        print("   Recorded 60 verified decisions (Status: OBSERVE)")

        # 2. Compile candidate Fast Path
        print("\n2. Compiling local candidate execution engine...")
        artifact_id = ink.compile(site, engine="exact")
        print(f"   Compiled candidate artifact: {artifact_id[:16]}... (Engine: exact)")

        # 3. Qualify against independent outcome evidence
        print("\n3. Calibrating candidate in shadow mode...")
        req = PromotionRequirements(
            min_samples=6,
            min_quality=0.5,
            min_confidence=0.5,
            max_degradation=0.8,
            comparison_rate=0.25,
            min_region_samples=3,
            evaluation_window=50,
        )
        ink.calibrate(site, verifier=verifier, requirements=req)
        print("   Candidate calibrated (Status: SHADOW)")

        print("   Observing shadow traffic against real production outcomes...")
        for i in range(20):
            state = training_states[i % len(training_states)]
            res = ink.decide(
                site=site,
                state=state,
                task_id=f"shadow-{i}",
                fallback=lambda: simulate_expensive_frontier_model(state),
            )
            ink.record_outcome(
                res.decision_id,
                Outcome(
                    quality=1.0,
                    verifier="workflow_verifier",
                    verifier_version="v1",
                    evidence={"success": True},
                ),
            )

        eval_result = ink.evaluate(site, verifier=verifier)
        site_state = ink.status(site)["state"]
        print(f"   Qualification Status: {site_state} (Qualified: {eval_result['qualified']})")

        # 4. Serve live traffic via local Fast Path
        print("\n4. Serving live production traffic:")
        test_states = [
            {"priority": "low", "retries": 0},
            {"priority": "critical", "retries": 0},
            {"priority": "medium", "retries": 1},
        ]
        for state in test_states:
            res = ink.decide(
                site=site,
                state=state,
                fallback=lambda: simulate_expensive_frontier_model(state),
            )
            src_label = "⚡ FAST PATH (LOCAL)" if res.source == "fast_path" else "🔄 FALLBACK (MODEL)"
            print(f"   State: {state} -> Choice: {res.choice!r} | Source: {src_label}")

        print("\n" + "=" * 65)
        print("  Quickstart completed successfully!")
        print("=" * 65)


if __name__ == "__main__":
    main()
