"""Tool Routing Showcase Example.

Demonstrates an AI agent selecting among bounded tools:
- search_kb
- issue_refund
- escalate_human

Ink observes repeated queries, verifies outcomes, qualifies a local
Fast Path, and serves subsequent matching decisions in sub-milliseconds.
Novel queries continue to fall back to the Host model.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from ink import DecisionSite, Ink, Outcome, PromotionRequirements


def mock_host_llm(state: dict[str, str]) -> str:
    """Simulates a remote frontier model making tool decisions."""
    query = state.get("query", "").lower()
    if "refund" in query or "charge" in query:
        return "issue_refund"
    if "human" in query or "agent" in query or "complaint" in query:
        return "escalate_human"
    return "search_kb"


def main():
    print("=" * 65)
    print("  Ink Example: Bounded Tool Routing with Fast Path Compilation")
    print("=" * 65)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "tool_routing.db")
        ink = Ink(db_path)

        site = DecisionSite(
            name="agent.tool_routing",
            description="Agent tool selection boundary",
            state_schema={"query": "string"},
            choices=("search_kb", "issue_refund", "escalate_human"),
        )
        ink.register(site)

        print("\n1. Observing production decisions and recording outcomes...")
        training_queries = [
            "how do I reset my password?",
            "where are the documentation files?",
            "can I get a refund for last month?",
            "why was my card charged twice?",
            "I want to speak with a human agent",
        ] * 12

        for i, q in enumerate(training_queries):
            state = {"query": q}
            res = ink.decide(
                site=site,
                state=state,
                task_id=f"init-{i}",
                fallback=lambda s=state: mock_host_llm(s),
            )
            # Record verified outcome
            expected = mock_host_llm(state)
            quality = 1.0 if res.choice == expected else 0.0
            ink.record_outcome(
                res.decision_id,
                quality=quality,
                verifier="tool_execution_verifier",
                verifier_version="v1",
            )

        print("   Recorded 60 decisions and verified outcomes.")

        # Trigger compilation and shadow qualification
        print("\n2. Compiling and qualifying local Fast Path...")
        ink.compile(site)

        req = PromotionRequirements(
            min_samples=10,
            min_quality=0.9,
            min_confidence=0.5,
            max_degradation=0.1,
            comparison_rate=0.2,
            min_region_samples=3,
            evaluation_window=50,
        )
        ink.maintenance(requirements=req)

        # Feed calibration traffic to pass shadow verification
        for i in range(25):
            q = training_queries[i % len(training_queries)]
            state = {"query": q}
            r = ink.decide(
                site=site,
                state=state,
                task_id=f"calib-{i}",
                fallback=lambda s=state: mock_host_llm(s),
            )
            expected = mock_host_llm(state)
            ink.record_outcome(
                r.decision_id,
                quality=1.0 if r.choice == expected else 0.0,
                verifier="tool_execution_verifier",
                verifier_version="v1",
            )

        ink.maintenance(requirements=req)
        status = ink.status(site)
        print(f"   Site status: {status['state']} (Engine: {status.get('engine', 'none')})")

        # 3. Test runtime serving
        print("\n3. Serving runtime traffic:")
        test_cases = [
            ("how do I reset my password?", "Repeated query"),
            ("can I get a refund for last month?", "Repeated query"),
            ("unusual novel enterprise SAML query with SSO", "Novel query (unseen)"),
        ]

        for query, label in test_cases:
            t0 = time.perf_counter()
            res = ink.decide(
                site=site,
                state={"query": query},
                fallback=lambda q=query: mock_host_llm({"query": q}),
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            src = "FAST_PATH (LOCAL)" if res.source == "fast_path" else "HOST FALLBACK"
            print(f"   [{label}]")
            print(f"     Query:  '{query}'")
            print(f"     Choice: '{res.choice}' | Source: {src} | Latency: {elapsed_ms:.2f}ms\n")

    print("=" * 65)
    print("  Tool routing completed successfully.")
    print("=" * 65)


if __name__ == "__main__":
    main()
