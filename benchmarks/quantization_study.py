"""Quantization study comparing FP16, INT8, and INT4 for Microloop Decision Model v1."""

from __future__ import annotations

import json
import resource
import sys
import time
from pathlib import Path

import mlx.nn as nn
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python/microloop"))

from microloop.internal.model.agent import Agent

from benchmarks.evaluate_all import compute_metrics


def main():
    benchmarks_dir = Path(__file__).resolve().parent
    data_dir = benchmarks_dir / "data"
    test_rows = [json.loads(line) for line in (data_dir / "test.jsonl").read_text().strip().split("\n")]
    subset_3way = [r for r in test_rows if len(r["choices"]) == 3]
    choices_3way = ["refund", "request_information", "specialist"]
    
    checkpoint_dir = Path(".microloop/models/microloop-decision-v1").resolve()
    
    print("=" * 60)
    print("MICROLOOP DECISION MODEL V1 - QUANTIZATION STUDY")
    print("=" * 60)
    
    report = {}
    
    for mode in ("fp16", "int8", "int4"):
        print(f"\nEvaluating precision mode: {mode.upper()}...")
        t0 = time.time()
        agent = Agent(str(checkpoint_dir), dtype="float16")
        
        predicate = lambda p, m: isinstance(m, nn.Linear) and m.weight.shape[-1] % 64 == 0
        if mode == "int8":
            nn.quantize(agent.model, bits=8, class_predicate=predicate)
            agent._inference = agent.model
        elif mode == "int4":
            nn.quantize(agent.model, bits=4, class_predicate=predicate)
            agent._inference = agent.model
        
        cold_load = (time.time() - t0) * 1000
        
        preds, probs_list, true_labels = [], [], []
        latencies = []
        for r in subset_3way:
            q = {"decision": {"type": "choice", "criteria": choices_3way, "instructions": r.get("instructions", "Choose the next action.")}}
            t_start = time.time()
            res = agent.predict(r["state"], q)["answers"]["decision"]
            latencies.append((time.time() - t_start) * 1000)
            preds.append(res["choice"])
            p_vec = [res["probabilities"][c] for c in choices_3way]
            probs_list.append(p_vec)
            true_labels.append(r["choice"])
            
        latencies.sort()
        probs_arr = np.array(probs_list)
        
        # Estimate quantized size
        base_size = (checkpoint_dir / "model.safetensors").stat().st_size / (1024 * 1024)
        if mode == "int8":
            est_size = base_size * 0.52
        elif mode == "int4":
            est_size = base_size * 0.28
        else:
            est_size = base_size
            
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
        
        metrics = compute_metrics(true_labels, preds, probs_arr, choices_3way)
        metrics.update({
            "p50_latency_ms": round(latencies[len(latencies)//2], 2),
            "p95_latency_ms": round(latencies[int(len(latencies)*0.95)], 2),
            "cold_load_ms": round(cold_load, 1),
            "peak_rss_mb": round(peak_rss, 1),
            "estimated_artifact_size_mb": round(est_size, 1),
        })
        report[mode] = metrics
        print(f"Results for {mode.upper()}: Accuracy={metrics['accuracy']}, Macro F1={metrics['macro_f1']}, p50={metrics['p50_latency_ms']}ms, ECE={metrics['ece']}")
    
    out_file = benchmarks_dir / "results/quantization_report.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(report, indent=2) + "\n")
    print("\nQuantization Study Complete:")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
