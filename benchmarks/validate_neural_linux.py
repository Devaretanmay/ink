"""Validate neural execution on Linux and macOS:
1. Checkpoint verification
2. Model load
3. Tokenization
4. Single Choice inference
5. Batch inference
6. Compile
7. Calibrate
8. Evaluate
9. Active serving
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python/ink"))

from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements
from ink.internal.engines import DecisionModelEngine
from ink.internal.model import RUNTIME_VERSION
from ink.internal.model.agent import Agent, collate_items
from ink.internal.model.registry import model_path

SITE = DecisionSite("linux.neural.site", {"request": "string"}, ("refund", "request_information", "specialist"))
REQ = PromotionRequirements(5, 0.5, 0.5, 0.9, 0.25, 3, 100)

def main():
    print("=" * 60)
    print("NEURAL INFERENCE VALIDATION SUITE (LINUX/MACOS)")
    print(f"Platform: {platform.platform()}")
    print(f"Python: {platform.python_version()}")
    print(f"Runtime Version: {RUNTIME_VERSION}")
    print("=" * 60)
    
    trained_p = Path(".ink/models/ink-decision-v1").resolve()
    mp = trained_p if (trained_p / "model.safetensors").is_file() else Path(os.environ.get("INK_MODEL_DIR", str(model_path())))
    print(f"\n1. CHECKPOINT VERIFICATION at {mp}...")
    assert (mp / "model.safetensors").is_file(), f"Missing model.safetensors at {mp}"
    assert (mp / "encoder/config.json").is_file(), "Missing encoder/config.json"
    assert (mp / "rl_agent_config.json").is_file(), "Missing rl_agent_config.json"
    assert (mp / "tokenizer/tokenizer.json").is_file(), "Missing tokenizer.json"
    print("   All checkpoint files present and verified.")
    
    print("\n2. MODEL LOAD...")
    # On Linux x86_64, MLX uses CPU. On Apple Silicon, Metal or CPU.
    agent = Agent(str(mp), dtype="float16" if platform.system() == "Darwin" else "float32")
    print(f"   Model loaded on device: {agent.device}, dtype: {agent.dtype}")
    
    print("\n3. TOKENIZATION...")
    tokens = agent.tok("I was billed twice. Please refund the duplicate charge.")
    assert len(tokens["input_ids"]) > 5
    print(f"   Tokenized sentence successfully into {len(tokens['input_ids'])} token IDs.")
    
    print("\n4. SINGLE CHOICE INFERENCE...")
    state = {"request": "I was billed twice. Please refund the duplicate charge."}
    q = {"decision": {"type": "choice", "criteria": ["refund", "request_information", "specialist"], "instructions": "Choose action."}}
    t0 = time.time()
    res = agent.predict(state, q)["answers"]["decision"]
    dt_single = (time.time() - t0) * 1000
    print(f"   Inference result: choice={res['choice']}, conf={res['confidence']} in {dt_single:.1f}ms")
    assert res["choice"] in ["refund", "request_information", "specialist"]
    
    print("\n5. BATCH INFERENCE...")
    batch_states = [
        {"request": "I was billed twice. Please refund the duplicate charge."},
        {"request": "Where is my pending deposit from yesterday?"},
        {"request": "My card was stolen by a thief in the subway."},
    ]
    t0 = time.time()
    batch_items, internal = [], []
    for s in batch_states:
        its, ins = agent.prepare(s, q)
        batch_items.extend(its)
        internal.extend(ins)
    collated = collate_items(batch_items, agent.tok.pad_token_id)
    logits, act = agent.forward(collated)
    dt_batch = (time.time() - t0) * 1000
    print(f"   Batch forward of 3 items finished in {dt_batch:.1f}ms: logits shape {logits.shape}")
    assert logits.shape[0] == 3
    
    print("\n6-9. DECISION JIT COMPILE, CALIBRATE, EVALUATE & ACTIVE SERVING...")
    engine = DecisionModelEngine(checkpoint=str(mp))
    with Ink(":memory:", engines=(engine,)) as client:
        client.register(SITE)
        # Populate history
        for i in range(120):
            req = batch_states[i % len(batch_states)]["request"]
            exp = "refund" if "refund" in req else ("request_information" if "deposit" in req else "specialist")
            dec = client.decide(site=SITE, state={"request": req}, task_id=f"init-{i}", fallback=lambda exp=exp: FallbackResult(exp, 1))
            client.record_outcome(dec.decision_id, quality=1.0, verifier="verifier", verifier_version="1.0", evidence={"exp": exp})
        
        # 6. Compile
        art_id = client.compile(SITE, engine="decision")
        assert art_id
        print(f"   6. Compiled artifact: {art_id[:12]}")
        
        # 7. Calibrate & Shadow
        for i in range(60):
            req = batch_states[i % len(batch_states)]["request"]
            exp = "refund" if "refund" in req else ("request_information" if "deposit" in req else "specialist")
            dec = client.decide(site=SITE, state={"request": req}, task_id=f"shad-{i}", fallback=lambda exp=exp: FallbackResult(exp, 1))
            client.record_outcome(dec.decision_id, quality=1.0, verifier="verifier", verifier_version="1.0", evidence={"exp": exp})
        
        # 8. Evaluate & Promote
        def ver(state, choice):
            req = state.get("request", "")
            exp = "refund" if "refund" in req else ("request_information" if "deposit" in req else "specialist")
            return Outcome(1.0 if choice == exp else 0.0, "verifier", "1.0", {"exp": exp})
            
        maint = client.maintenance(verifier=ver, requirements=REQ, engine="decision")
        print("   Maintenance output:", json.dumps(maint, indent=2))
        assert client.inspect(SITE)["state"] == "ACTIVE"
        print("   8. Evaluation passed! Promoted to ACTIVE state.")
        
        # 9. Active Serving
        hits = 0
        for i in range(30):
            req = batch_states[i % len(batch_states)]["request"]
            exp = "refund" if "refund" in req else ("request_information" if "deposit" in req else "specialist")
            dec = client.decide(site=SITE, state={"request": req}, task_id=f"act-{i}", fallback=lambda exp=exp: FallbackResult(exp, 1))
            if dec.source == "fast_path":
                hits += 1
        print(f"   9. Active serving verified: {hits}/30 fast_path hits.")
        assert hits > 0
        
    print("\n" + "=" * 60)
    print("ALL 9 NEURAL INFERENCE CRITERIA VALIDATED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    main()
