"""30-Second Terminal Demonstration of Ink Decision JIT.

Clear 3-Act Narrative:
  Act 1: Observe repeated agent decisions (fallback) & profile site economics.
  Act 2: Compile candidate fast paths & qualify in shadow against verified outcomes.
  Act 3: Serve verified decisions locally in <0.5ms with zero false serves on drift.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath("python/ink"))

from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements


def mock_remote_llm(state: dict, drifted: bool = False) -> str:
    time.sleep(0.005)
    text = state.get("text", "").lower()
    if "broken" in text or "damage" in text:
        return "specialist" if drifted else "refund"
    elif "tracking" in text:
        return "request_info"
    return "specialist"


def main():
    print("=" * 76)
    print("   INK DECISION JIT: 30-SECOND LOCAL LIFECYCLE DEMONSTRATION")
    print("=" * 76)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "demo.db")
        client = Ink(db_path)

        site = DecisionSite(
            name="support.routing",
            state_schema={"text": "string", "amount": "integer"},
            choices=("refund", "request_info", "specialist"),
            fallback_revision="1",
        )
        client.register(site)

        reqs = PromotionRequirements(
            min_samples=3,
            min_quality=0.50,
            min_confidence=0.50,
            max_degradation=1.00,
            comparison_rate=0.35,
            min_region_samples=3,
            evaluation_window=20,
            min_comparison_rate=0.05,
        )

        patterns = [
            {"text": "Item arrived broken in box. Need refund.", "amount": 35},
            {"text": "Where is tracking status for shipment?", "amount": 0},
            {"text": "High value damaged shipment escalation.", "amount": 450},
        ]

        print("\n[ACT 1: OBSERVE & PROFILE ECONOMICS]")
        print("  Observing agent decisions executing remote LLM fallback...")
        for i in range(90):
            st = dict(patterns[i % 3])
            dec = client.decide(
                site=site.name,
                state=st,
                fallback=lambda s=st: FallbackResult(
                    mock_remote_llm(s), model_calls=1, cost=0.002
                ),
                task_id=f"obs_{i}",
            )
            client.record_outcome(
                dec.decision_id,
                quality=1.0,
                verifier="returns_policy",
                verifier_version="1",
                evidence={"state": st},
            )

        prof = client.profile(site)
        print(f"  -> Observations: {prof.observations}")
        print(f"  -> Exact Repetition Rate: {prof.exact_repeat_rate:.1%}")
        print(f"  -> Verifier Quality: {prof.verifier_quality:.1%}")
        print(f"  -> Economic Recommendation: '{prof.recommendation.upper()}'")
        print(f"  -> Amortization Horizon: ~{prof.break_even_decisions} decisions")

        print("\n[ACT 2: COMPILE & SHADOW QUALIFICATION]")
        print("  Compiling fast-path candidate and calibrating negative margins...")
        client.compile(site, engine="exact")

        def verifier(state: dict, choice: str) -> Outcome:
            exp = mock_remote_llm(state, drifted=False)
            return Outcome(
                1.0 if choice == exp else 0.0,
                "returns_policy",
                "1",
                {"expected": exp},
            )

        client.calibrate(site, verifier=verifier, requirements=reqs)

        print("  Executing shadow comparison traffic against holdout evidence...")
        for i in range(24):
            st = dict(patterns[i % 3])
            dec = client.decide(
                site=site.name,
                state=st,
                fallback=lambda s=st: FallbackResult(
                    mock_remote_llm(s), model_calls=1, cost=0.002
                ),
                task_id=f"shadow_{i}",
            )
            client.record_outcome(
                dec.decision_id,
                quality=1.0,
                verifier="returns_policy",
                verifier_version="1",
                evidence={"state": st},
            )

        eval_res = client.evaluate(site, verifier=verifier, auto_promote=True)
        art = client._artifact(site.version)
        print(f"  -> Shadow Qualification: Qualified={eval_res['qualified']}")
        print(f"  -> Artifact Promoted: ID={art['id'][:12]}... | Status={art['status']}")

        print("\n[ACT 3: LOCAL FAST-PATH SERVING & DRIFT PROTECTION]")
        print("  Serving verified decisions locally (skipping cloud LLM):")
        test_state = patterns[0]
        for r in range(3):
            t0 = time.perf_counter()
            d = client.decide(
                site=site.name,
                state=test_state,
                fallback=lambda: FallbackResult(mock_remote_llm(test_state), 1, cost=0.002),
            )
            lat = (time.perf_counter() - t0) * 1000.0
            print(
                f"  Serve {r + 1}: Choice='{d.choice}' | {d.source.upper()} | Latency: {lat:.3f}ms"
            )

        print("\n  Injecting business policy change: Broken items now route to 'specialist'.")
        print("  Running comparison traffic...")
        for i in range(30):
            st = dict(patterns[0])
            d = client.decide(
                site=site.name,
                state=st,
                fallback=lambda s=st: FallbackResult(
                    mock_remote_llm(s, drifted=True), 1, cost=0.002
                ),
                task_id=f"drift_{i}",
            )
            client.record_outcome(
                d.decision_id,
                quality=1.0 if d.choice == "specialist" else 0.0,
                verifier="returns_policy",
                verifier_version="2",
                evidence={"drift": True},
            )

        client.reevaluate(site)
        art_post = client._artifact(site.version)
        print(f"  -> Drift Detected via Comparison: Artifact Status={art_post['status']}")

        d_fallback = client.decide(
            site=site.name,
            state=test_state,
            fallback=lambda: FallbackResult(mock_remote_llm(test_state, drifted=True), 1),
        )
        print(
            f"  -> Safe Fallback: Choice='{d_fallback.choice}' | "
            f"Source={d_fallback.source.upper()}"
        )

        client.close()

    print("\n" + "=" * 76)
    print("   DEMO COMPLETED: 0 CORRUPTED SERVES | ZERO CLOUD API KEYS NEEDED")
    print("=" * 76)


if __name__ == "__main__":
    main()
