"""Validation script for the complete Ink Semantic Coverage Lifecycle:
OBSERVE -> COMPILE -> CALIBRATE (SHADOW) -> SHADOW EVIDENCE -> EVALUATE (ACTIVE)
-> ACTIVE SERVING -> DRIFT / DEGRADATION -> DEMOTE -> SAFE FALLBACK.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath("python/ink"))

from ink.decision_api import Ink
from ink.internal.contracts import DecisionSite, Outcome, PromotionRequirements


def make_site():
    return DecisionSite(
        name="finance.support.triage",
        state_schema={"query": "string"},
        choices=("refund", "investigate", "escalate"),
        fallback_revision="1",
    )


def test_complete_semantic_lifecycle():
    print("=== Step 1: Initializing Lifecycle Test Environment ===")
    site = make_site()
    req = PromotionRequirements(
        min_samples=10,
        min_quality=0.50,
        min_confidence=0.50,
        max_degradation=0.65,
        comparison_rate=0.05,
        min_region_samples=5,
        evaluation_window=100,
        max_uncovered_rate=0.50,
    )

    def verifier(state, choice):
        # Ground truth rule: refund for duplicate/double charges, investigate for wire status
        query = state.get("query", "").lower()
        if "duplicate" in query or "double" in query or "charged twice" in query:
            expected = "refund"
        elif "wire" in query or "transfer" in query or "clearance" in query:
            expected = "investigate"
        else:
            expected = "escalate"
        quality = 1.0 if choice == expected else 0.0
        return Outcome(
            quality=quality,
            verifier="support_v1",
            verifier_version="1",
            evidence={"rule": "match"},
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "lifecycle.db")
        with Ink(db_path) as client:
            print("=== Step 2: OBSERVE - Recording historical observations ===")
            training_queries = [
                ("I was charged twice on my card", "refund"),
                ("Status of incoming wire transfer", "investigate"),
                ("Suspicious activity on my account", "escalate"),
            ] * 80  # 240 total samples across 3 distinct intent clusters

            for query, choice in training_queries:
                res = client.decide(
                    site=site,
                    state={"query": query},
                    fallback=lambda c=choice: c,
                )
                assert res.source == "fallback"
                out = verifier({"query": query}, res.choice)
                client.record_outcome(
                    res.decision_id,
                    quality=out.quality,
                    verifier=out.verifier,
                    verifier_version=out.verifier_version,
                    evidence=out.evidence,
                )

            print("=== Step 3: COMPILE - Compiling candidate artifact ===")
            artifact_id = client.compile(site, engine="exact")
            inspect_info = client.inspect(site)
            assert inspect_info["fast_path"] == artifact_id
            assert inspect_info["state"] == "SHADOW"
            print(f"Candidate compiled: {artifact_id[:12]} (state={inspect_info['state']})")

            print("=== Step 4: CALIBRATE - Calibrating boundaries (Regions start in SHADOW) ===")
            profile = client.calibrate(site, verifier=verifier, requirements=req)
            cov_engine_data = profile["coverage_engine"]
            semantic_regions = cov_engine_data["semantic_regions"]
            print(f"Derived {len(semantic_regions)} semantic regions:")
            for sr in semantic_regions:
                print(
                    f"  - Region {sr['region_id']} ({sr['choice']}): radius={sr['radius']}, "
                    f"neg_margin={sr['negative_margin']}, status={sr['status']}"
                )
                assert sr["status"] == "SHADOW"
                assert sr["radius"] <= sr["negative_margin"]

            print("=== Step 5: SHADOW TRAFFIC - Unseen queries during shadow evaluation ===")
            unseen_refund = [
                "I was charged twice on my subscription",
                "Charged twice on my debit card statement",
                "I was charged twice on my account",
                "Charged twice on my receipt",
                "I was charged twice for monthly plan",
                "Charged twice on my visa card",
            ] * 4  # 24 samples
            for query in unseen_refund:
                res = client.decide(
                    site=site,
                    state={"query": query},
                    fallback=lambda: "refund",
                )
                assert res.source == "fallback"
                assert res.fallback_reason == "shadow"
                out = verifier({"query": query}, res.choice)
                client.record_outcome(
                    res.decision_id,
                    quality=out.quality,
                    verifier=out.verifier,
                    verifier_version=out.verifier_version,
                    evidence=out.evidence,
                )

            # Also provide fresh exact holdout traffic (>= min_region_samples for each)
            exact_queries = [
                ("I was charged twice on my card", "refund"),
                ("Status of incoming wire transfer", "investigate"),
                ("Suspicious activity on my account", "escalate"),
            ] * 20
            for query, choice in exact_queries:
                res = client.decide(site=site, state={"query": query}, fallback=lambda c=choice: c)
                out = verifier({"query": query}, res.choice)
                client.record_outcome(
                    res.decision_id,
                    quality=out.quality,
                    verifier=out.verifier,
                    verifier_version=out.verifier_version,
                    evidence=out.evidence,
                )

            # 5. EVALUATE: Evaluate candidate artifact and semantic regions
            print("=== Step 6: EVALUATE - Promoting qualified semantic regions ===")
            eval_result = client.evaluate(site, verifier=verifier)
            assert eval_result["qualified"] is True
            inspect_after = client.inspect(site)
            assert inspect_after["state"] == "ACTIVE"
            qualified_sem = eval_result.get("qualified_semantic_regions", [])
            print(f"Artifact promoted to ACTIVE! Qualified semantic regions: {qualified_sem}")
            assert len(qualified_sem) > 0

            # 6. ACTIVE SERVING: Test novel unseen states served via semantic fast path!
            print("=== Step 7: ACTIVE SERVING - Testing semantic fast path serving ===")
            novel_test_queries = [
                ("I was charged twice on my statement", "refund"),
                ("Charged twice on my bank account", "refund"),
            ]
            for query, expected_choice in novel_test_queries:
                res = client.decide(
                    site=site,
                    state={"query": query},
                    fallback=lambda: "refund",
                )
                print(
                    f"Query: '{query}' -> source={res.source}, choice={res.choice}, "
                    f"confidence={res.confidence}"
                )
                assert res.source == "fast_path"
                assert res.choice == expected_choice

            # 7. ABSTENTION: Contrastive negatives, ambiguous, or far-away states must abstain!
            print("=== Step 8: ABSTENTION - Testing contrastive counterexamples ===")
            counterexamples = [
                "Please wire money to my account",  # wire transfer, not refund
                "How do I open a new credit card account?",  # outside coverage
            ]
            for query in counterexamples:
                res = client.decide(
                    site=site,
                    state={"query": query},
                    fallback=lambda: "investigate",
                )
                print(f"Counterexample query: '{query}' -> source={res.source}, reason={res.fallback_reason}")
                assert res.source == "fallback"
                assert res.fallback_reason in ("outside_coverage", "insufficient_confidence")

            # 8. DRIFT & DEMOTION: Inject degraded outcomes / verifier mismatch
            print("=== Step 9: DRIFT & DEMOTION - Injecting degraded outcomes ===")
            for i in range(25):
                res = client.decide(
                    site=site,
                    state={"query": f"Degraded query sample {i}"},
                    fallback=lambda: "refund",
                )
                client.record_outcome(
                    res.decision_id,
                    quality=0.1,  # Severe failure / drift
                    verifier="support_v1",
                    verifier_version="1",
                    evidence={"failure": "hallucinated"},
                )

            # Re-evaluate drift check
            reeval = client.reevaluate(site)
            print(f"Re-evaluation drift check result: demoted={reeval.get('demoted')}")
            assert reeval.get("demoted") is True
            inspect_demoted = client.inspect(site)
            assert inspect_demoted["state"] == "SHADOW"
            print("Artifact successfully demoted back to SHADOW!")

            # 9. AFTER DEMOTION: Fast paths must NOT be served
            print("=== Step 10: AFTER DEMOTION - Verifying safe fallback ===")
            res_after = client.decide(
                site=site,
                state={"query": "Billed twice for my order today"},
                fallback=lambda: "refund",
            )
            print(f"After demotion query: source={res_after.source}, reason={res_after.fallback_reason}")
            assert res_after.source == "fallback"
            assert res_after.choice == "refund"

    print("\nSUCCESS: Complete Semantic Coverage Lifecycle Validated!")


if __name__ == "__main__":
    test_complete_semantic_lifecycle()
