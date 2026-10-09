"""Reference Example B: Semantic DecisionSite (`ticket.triage`).

Demonstrates:
- Semantic NLP ticket triage boundary with string fields.
- LinearClassifierEngine with text vectorization and semantic coverage regions.
- Abstention on out-of-distribution or ambiguous inputs.
- Progressive qualification of semantic regions from verified outcomes.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements


def remote_llm_triage(state: dict) -> FallbackResult:
    """Simulates an expensive remote LLM triaging incoming customer support tickets."""
    msg = (state["subject"] + " " + state["message"]).lower()
    if any(w in msg for w in ("invoice", "refund", "charge", "payment", "subscription", "bill")):
        choice = "billing"
    elif any(w in msg for w in ("crash", "bug", "error", "exception", "failed", "timeout")):
        choice = "technical"
    else:
        choice = "general"

    return FallbackResult(
        choice=choice,
        model_calls=1,
        input_tokens=650,
        output_tokens=10,
        cost=0.005,
        provider="anthropic",
        model="claude-3-5-sonnet",
    )


def ticket_verifier(state: dict, choice: str) -> Outcome:
    """Ground truth verifier derived from support agent resolution or CSAT rating."""
    expected = remote_llm_triage(state).choice
    match = choice == expected
    return Outcome(
        quality=1.0 if match else 0.0,
        verifier="support_resolution_audit",
        verifier_version="v1",
        evidence={"resolved": match, "category": choice},
    )


def main():
    print("=" * 70)
    print("  Ink Reference Example B: Semantic NLP Ticket Triage")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "semantic.db")
        ink = Ink(db_path)

        site = DecisionSite(
            name="ticket.triage",
            description="Customer support inquiry topic routing",
            state_schema={"subject": "string", "message": "string", "priority": "string"},
            choices=("billing", "technical", "general"),
        )

        # Diverse semantic traffic clusters
        training_corpus = [
            # Billing cluster
            {"subject": "Refund request", "message": "I was charged twice for subscription", "priority": "high"},
            {"subject": "Billing issue", "message": "Please send my latest monthly invoice", "priority": "medium"},
            {"subject": "Payment failed", "message": "Credit card payment declined for bill", "priority": "high"},
            # Technical cluster
            {"subject": "API 500 error", "message": "Endpoints timing out with server error", "priority": "high"},
            {"subject": "App crash on login", "message": "Uncaught exception crash when opening", "priority": "critical"},
            {"subject": "Bug report", "message": "Memory leak exception in worker process", "priority": "medium"},
            # General cluster
            {"subject": "Feature inquiry", "message": "Do you support dark mode on mobile?", "priority": "low"},
            {"subject": "Office address", "message": "Where is your corporate headquarters?", "priority": "low"},
        ]

        print("\n1. Observing semantic decision stream across natural language inquiries...")
        for i in range(160):
            state = training_corpus[i % len(training_corpus)]
            res = ink.decide(
                site=site,
                state=state,
                task_id=f"ticket-{i}",
                fallback=lambda: remote_llm_triage(state),
            )
            ink.record_outcome(
                res.decision_id,
                ticket_verifier(state, res.choice),
            )

        print("2. Compiling LinearClassifierEngine candidate with semantic coverage...")
        ink.compile(site, engine="linear")

        req = PromotionRequirements(
            min_samples=8,
            min_quality=0.5,
            min_confidence=0.25,
            max_degradation=0.8,
            min_region_samples=3,
            evaluation_window=50,
        )
        ink.calibrate(site, verifier=ticket_verifier, requirements=req)
        print("   Calibrated candidate into SHADOW mode.")

        print("3. Observing shadow traffic with real ticket outcomes...")
        for i in range(40):
            state = training_corpus[i % len(training_corpus)]
            res = ink.decide(
                site=site,
                state=state,
                task_id=f"shadow-ticket-{i}",
                fallback=lambda: remote_llm_triage(state),
            )
            ink.record_outcome(
                res.decision_id,
                ticket_verifier(state, res.choice),
            )

        ink.evaluate(site, verifier=ticket_verifier)
        status = ink.status(site)
        print(f"   Site status: {status['state']} (Fast Path Revision: {status['active_revision']})")

        print("\n4. Serving incoming live tickets:")
        test_queries = [
            # In-distribution semantic matches
            {"subject": "Invoice missing", "message": "Where can I download my subscription invoice?", "priority": "medium"},
            {"subject": "Fatal crash", "message": "Client throws timeout error on boot", "priority": "high"},
            # Novel out-of-distribution input (should safely fallback to remote LLM)
            {"subject": "Partnership inquiry", "message": "Would like to discuss enterprise partnership", "priority": "low"},
        ]

        for q in test_queries:
            res = ink.decide(
                site=site,
                state=q,
                fallback=lambda: remote_llm_triage(q),
            )
            src_label = "⚡ FAST PATH" if res.source == "fast_path" else "🔄 FALLBACK (MODEL)"
            print(f"   [{src_label}] '{q['subject']}' -> {res.choice} (Reason: {res.fallback_reason or 'Qualified Region'})")

        print("\n" + "=" * 70)
        print("  Example B completed successfully.")
        print("=" * 70)


if __name__ == "__main__":
    main()
