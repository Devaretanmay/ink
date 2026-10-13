"""Customer Support Triage Routing Example.

Demonstrates routing incoming support messages into bounded categories:
- billing
- technical
- account

Shows observation, outcome recording, and runtime dispatch.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from ink import DecisionSite, Ink


def mock_support_llm(state: dict[str, str]) -> str:
    """Mock Host model simulating customer support triage."""
    text = (state.get("subject", "") + " " + state.get("body", "")).lower()
    if any(k in text for k in ("invoice", "payment", "card", "refund", "subscription")):
        return "billing"
    if any(k in text for k in ("error", "bug", "crash", "stacktrace", "api", "500")):
        return "technical"
    return "account"


def main():
    print("=" * 65)
    print("  Ink Example: Support Ticket Triage Routing")
    print("=" * 65)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "support.db")
        ink = Ink(db_path)

        site = DecisionSite(
            name="support.triage",
            description="Inbound support ticket dispatch",
            state_schema={"subject": "string", "body": "string"},
            choices=("billing", "technical", "account"),
        )
        ink.register(site)

        samples = [
            {"subject": "Invoice Question", "body": "Where can I download my March invoice?"},
            {"subject": "API 500 error", "body": "Webhook delivery is failing with HTTP 500."},
            {"subject": "Update team members", "body": "Need to remove an engineer from our org."},
        ]

        print("\nProcessing inbound support inquiries...")
        for i, s in enumerate(samples, 1):
            t0 = time.perf_counter()
            res = ink.decide(
                site=site,
                state=s,
                task_id=f"ticket-{i}",
                fallback=lambda st=s: mock_support_llm(st),
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000

            # Record verified outcome once customer inquiry resolves
            expected = mock_support_llm(s)
            ink.record_outcome(
                res.decision_id,
                quality=1.0 if res.choice == expected else 0.0,
                verifier="support_resolution_team",
                verifier_version="v1",
            )

            print(f"  Ticket #{i}: '{s['subject']}'")
            print(f"    Routing Choice: '{res.choice}' (Source: {res.source})")
            print(f"    Dispatch Time:  {elapsed_ms:.2f}ms\n")

    print("=" * 65)
    print("  Support routing completed.")
    print("=" * 65)


if __name__ == "__main__":
    main()
