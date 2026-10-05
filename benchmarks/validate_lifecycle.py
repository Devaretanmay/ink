"""Validate Ink Decision Model v1 through the full decision lifecycle:
OBSERVE -> COMPILE -> CALIBRATE -> SHADOW -> EVALUATE -> ACTIVE -> DRIFT -> DEMOTE
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python/ink"))

from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements
from ink.internal.engines import DecisionModelEngine

SITE = DecisionSite("refund.lifecycle", {"request": "string"}, ("refund", "request_information", "specialist"))
REQ = PromotionRequirements(10, 0.5, 0.5, 0.8, 0.25, 5, 100)

REFUND_TEXT = "I was billed twice. Please refund the duplicate charge."
INFO_TEXT = "Where is my pending deposit from yesterday?"
SPEC_TEXT = "My card was stolen by a thief in the subway."

def mock_verifier(state, choice):
    req = state.get("request", "")
    if req == REFUND_TEXT:
        expected = "refund"
    elif req == INFO_TEXT:
        expected = "request_information"
    else:
        expected = "specialist"
    quality = 1.0 if choice == expected else 0.0
    return Outcome(quality, "ledger-verifier", "1.0", {"expected": expected})

def run_traffic(client, count, phase_name, bad=False):
    results = []
    cases = [REFUND_TEXT, INFO_TEXT, SPEC_TEXT]
    for i in range(count):
        req = cases[i % len(cases)]
        state = {"request": req}
        expected = "refund" if req == REFUND_TEXT else ("request_information" if req == INFO_TEXT else "specialist")
        
        result = client.decide(
            site=SITE,
            state=state,
            task_id=f"{phase_name}-{i}",
            fallback=lambda state=state, exp=expected: FallbackResult(exp, model_calls=1),
        )
        
        outcome = mock_verifier(state, result.choice)
        if bad:
            outcome = Outcome(0.0, "ledger-verifier", "1.0", {"expected": "forced_drift"})
        client.record_outcome(result.decision_id, **asdict(outcome))
        results.append(result)
    return results

def main():
    checkpoint = Path(".ink/models/ink-decision-v1").resolve()
    print("=" * 60)
    print("INK DECISION MODEL V1 - LIFECYCLE VALIDATION")
    print(f"Checkpoint: {checkpoint}")
    print("=" * 60)
    
    engine = DecisionModelEngine(checkpoint=str(checkpoint))
    with Ink(":memory:", engines=(engine,)) as client:
        # Register site
        client.register(SITE)
        print("\n1. OBSERVE PHASE: Recording baseline observation decisions...")
        obs = run_traffic(client, 150, "observe")
        print(f"   Recorded {len(obs)} observation decisions.")
        
        print("\n2. COMPILE PHASE: Compiling DecisionModelEngine artifact...")
        compile_res = client.compile(SITE, engine="decision")
        assert compile_res is not None
        site_state = client.inspect(SITE)["state"]
        print(f"   Compiled artifact {compile_res[:12]}... Lifecycle state: {site_state}")
        assert site_state == "SHADOW"
        
        print("\n3. SHADOW PHASE: Running shadow traffic beside fallback...")
        shadow_results = run_traffic(client, 60, "shadow")
        print(f"   Ran {len(shadow_results)} shadow decisions.")
        
        print("\n4. EVALUATE & PROMOTE PHASE: Running maintenance evaluation...")
        eval_res = client.maintenance(verifier=mock_verifier, requirements=REQ, engine="decision")
        site_state = client.inspect(SITE)["state"]
        print(f"   Maintenance result: {json.dumps(eval_res, indent=2)}")
        print(f"   Lifecycle state after qualification: {site_state}")
        assert site_state == "ACTIVE", f"Expected ACTIVE, got {site_state}"
        
        print("\n5. ACTIVE SERVING PHASE: Serving live fast-path decisions...")
        active_results = run_traffic(client, 40, "active")
        fast_count = sum(1 for r in active_results if r.source == "fast_path")
        comp_count = sum(1 for r in active_results if r.fallback_reason == "comparison")
        print(f"   Served {len(active_results)} decisions: {fast_count} fast_path hits, {comp_count} comparison fallbacks.")
        assert fast_count > 0, "Expected at least one fast-path served decision"
        
        print("\n6. DRIFT INJECTION: Simulating outcome degradation...")
        drift_results = run_traffic(client, 60, "drift", bad=True)
        print(f"   Injected {len(drift_results)} degraded decisions.")
        
        print("\n7. DEMOTE PHASE: Running maintenance drift detection...")
        demote_res = client.maintenance(verifier=mock_verifier, requirements=REQ, engine="decision")
        site_state = client.inspect(SITE)["state"]
        print(f"   Maintenance result: {json.dumps(demote_res, indent=2)}")
        print(f"   Lifecycle state after drift detection: {site_state}")
        assert site_state == "SHADOW", f"Expected demotion to SHADOW, got {site_state}"
        
        print("\n8. AFTER DEMOTION: Verifying safe application fallback...")
        after_results = run_traffic(client, 3, "after")
        all_fallback = all(r.source == "fallback" for r in after_results)
        print(f"   After demotion decisions: all fallback = {all_fallback}")
        assert all_fallback, "Expected all decisions to fall back after demotion"
        
    print("\n" + "=" * 60)
    print("FULL LIFECYCLE VALIDATION PASSED: ALL 8 STAGES VERIFIED!")
    print("=" * 60)

if __name__ == "__main__":
    main()
