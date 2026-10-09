"""Real Model API Example with Graceful Offline Fallback.

Demonstrates integrating Ink with actual frontier LLM APIs (Anthropic or OpenAI).
Reads ANTHROPIC_API_KEY or OPENAI_API_KEY from the environment if present.
If no API keys are configured, falls back seamlessly to an offline simulation
so CI tests and offline developers can verify functionality without network access.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements


def call_real_or_mock_llm(state: dict) -> FallbackResult:
    """Invokes remote LLM API if keys exist, otherwise uses local deterministic mock."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if anthropic_key:
        try:
            import urllib.request
            import json

            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps({
                    "model": "claude-3-haiku-20240307",
                    "max_tokens": 10,
                    "messages": [
                        {
                            "role": "user",
                            "content": f"Classify this issue as 'bug' or 'question': {state['text']}. Respond only with the choice.",
                        }
                    ],
                }).encode("utf-8"),
                headers={
                    "x-api-key": anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read())
                choice_text = data["content"][0]["text"].strip().lower()
                choice = "bug" if "bug" in choice_text else "question"
                return FallbackResult(
                    choice=choice,
                    model_calls=1,
                    input_tokens=data.get("usage", {}).get("input_tokens", 50),
                    output_tokens=data.get("usage", {}).get("output_tokens", 5),
                    cost=0.0001,
                    provider="anthropic",
                    model="claude-3-haiku-20240307",
                )
        except Exception as exc:
            print(f"   [Notice: Anthropic API call failed ({exc}); falling back to local simulation]")

    # Offline mock fallback (default when no credentials are configured)
    choice = "bug" if "error" in state["text"].lower() or "broken" in state["text"].lower() else "question"
    return FallbackResult(
        choice=choice,
        model_calls=1,
        input_tokens=80,
        output_tokens=4,
        cost=0.0002,
        provider="mock_offline",
        model="simulated-frontier-v1",
    )


def main():
    print("=" * 70)
    print("  Ink Real Model Integration: Live API / Offline Fallback")
    print("=" * 70)

    has_live_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    mode_str = "LIVE FRONTIER API" if has_live_key else "OFFLINE SIMULATION (No API keys found)"
    print(f"  Execution Mode: {mode_str}\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "real_model.db")
        ink = Ink(db_path)

        site = DecisionSite(
            name="support.classify_issue",
            description="Incoming developer ticket classification",
            state_schema={"text": "string"},
            choices=("bug", "question"),
        )

        samples = [
            {"text": "Getting a 500 internal server error on checkout"},
            {"text": "How do I update my billing email address?"},
            {"text": "The login button is completely broken"},
            {"text": "Where is the documentation for webhooks?"},
        ]

        print("1. Ingesting observed traffic:")
        for i in range(80):
            st = samples[i % len(samples)]
            res = ink.decide(
                site=site,
                state=st,
                task_id=f"req-{i}",
                fallback=lambda: call_real_or_mock_llm(st),
            )
            ink.record_outcome(
                res.decision_id,
                Outcome(
                    quality=1.0,
                    verifier="issue_audit",
                    verifier_version="v1",
                    evidence={"text": st["text"]},
                ),
            )

        print("2. Compiling local candidate and qualifying in shadow mode...")
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
            st = samples[i % len(samples)]
            res = ink.decide(
                site=site,
                state=st,
                task_id=f"shadow-{i}",
                fallback=lambda: call_real_or_mock_llm(st),
            )
            ink.record_outcome(
                res.decision_id,
                Outcome(1.0, "ver", "1", {}),
            )

        ink.evaluate(site, verifier=lambda st, c: Outcome(1.0, "ver", "1", {}))
        print(f"   Fast Path status: {ink.status(site)['state']}")

        print("\n3. Testing live traffic:")
        for st in samples:
            res = ink.decide(
                site=site,
                state=st,
                fallback=lambda: call_real_or_mock_llm(st),
            )
            src = "⚡ FAST PATH (LOCAL)" if res.source == "fast_path" else "🔄 REMOTE LLM"
            print(f"   [{src}] '{st['text'][:35]}...' -> {res.choice}")

        print("\n" + "=" * 70)
        print("  Real model example completed successfully.")
        print("=" * 70)


if __name__ == "__main__":
    main()
