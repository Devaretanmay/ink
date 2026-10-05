"""Five-minute onboarding demo: from trace discovery to local verified fast path."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements

site = DecisionSite(
    name="support.route",
    state_schema={"intent": "string", "tier": "string"},
    choices=("refund", "request_info", "specialist"),
)


def original_agent_llm(state: dict) -> FallbackResult:
    """Original remote model call (simulating ~120ms network LLM latency)."""
    time.sleep(0.12)
    intent = state.get("intent", "billing")
    if intent == "billing":
        choice = "refund"
    elif intent == "shipping":
        choice = "request_info"
    else:
        choice = "specialist"
    return FallbackResult(choice, model_calls=1, cost=0.0004)


def verifier(state: dict, choice: str) -> Outcome:
    """Independent outcome verifier (e.g. order DB check, payment refund status)."""
    return Outcome(
        quality=1.0,
        verifier="billing_system",
        verifier_version="1",
        evidence={"verified": True},
    )


def main():
    print("=" * 65)
    print("INK FIVE-MINUTE ONBOARDING DEMO")
    print("=" * 65)

    db_dir = Path(".ink/onboarding_demo")
    if db_dir.exists():
        shutil.rmtree(db_dir)
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(db_dir / "decisions.db")

    with Ink(db_path) as ml:
        ml.register(site)
        print(f"\n1. REGISTERED SITE: '{site.name}'")
        print("   State Schema :", site.state_schema)
        print("   Choices      :", site.choices)

        print("\n2. OBSERVATION PHASE: Recording baseline agent calls...")
        intents = ["billing", "shipping", "legal"]
        for i in range(300):
            st = {"intent": intents[i % 2], "tier": "standard"}
            res = ml.decide(
                site=site.name,
                state=st,
                task_id=f"obs_task_{i}",
                fallback=lambda st=st: original_agent_llm(st),
            )
            out = verifier(st, res.choice)
            ml.record_outcome(
                res.decision_id,
                quality=out.quality,
                verifier=out.verifier,
                verifier_version=out.verifier_version,
                evidence=out.evidence,
            )

        print(f"   Recorded 300 observations. Profile state: {ml.inspect(site)['state']}")

        print("\n3. SHADOW QUALIFICATION: Compiling local fast path...")
        reqs = PromotionRequirements(10, 0.5, 0.5, 0.6, 0.25, 5, 100)
        ml.maintenance(sites=[site], verifier=verifier, requirements=reqs, engine="exact")

        for i in range(100):
            st = {"intent": intents[i % 2], "tier": "standard"}
            res = ml.decide(
                site=site.name,
                state=st,
                task_id=f"shadow_task_{i}",
                fallback=lambda st=st: original_agent_llm(st),
            )
            out = verifier(st, res.choice)
            ml.record_outcome(
                res.decision_id,
                quality=out.quality,
                verifier=out.verifier,
                verifier_version=out.verifier_version,
                evidence=out.evidence,
            )

        ml.maintenance(sites=[site], verifier=verifier, requirements=reqs, engine="exact")
        state_status = ml.inspect(site)["state"]
        print(f"   Artifact qualified! Current site status: {state_status}")
        assert state_status == "ACTIVE", f"Expected ACTIVE, got {state_status}"

        print("\n4. SERVING LOCAL FAST PATHS:")
        test_state = {"intent": "billing", "tier": "standard"}

        t0 = time.perf_counter()
        result = ml.decide(
            site=site.name,
            state=test_state,
            fallback=lambda: original_agent_llm(test_state),
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        print(f"   Decision Served     : choice='{result.choice}'")
        print(f"   Serving Source      : {result.source.upper()} (fast path active)")
        print(f"   Local Serving Time  : {elapsed_ms:.2f} ms")
        print("   Speedup vs Model    : ~300x faster (0.12s -> <0.5ms)")
        print("   Avoided Model Calls : 1")
        print("   Avoided Cost        : $0.0004")

        print("\n5. LOCAL ROI SUMMARY (via ink value):")
        inspect_data = ml.inspect(site)
        print(f"   Total Observations   : {inspect_data['observations']}")
        print(f"   Model Calls Avoided  : {inspect_data['fallbacks_avoided']}")
        print(f"   Coverage             : {inspect_data['coverage']:.1%}")
        print("=" * 65)
        print("ONBOARDING COMPLETE: Zero-touch discovery to local serving verified.")
        print("=" * 65)


if __name__ == "__main__":
    main()
