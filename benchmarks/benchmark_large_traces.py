"""Benchmark streaming trace discovery performance at 10k, 100k, and 1M records."""

from __future__ import annotations

import gc
import json
import os
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python/microloop"))

from microloop.discovery import CanonicalTrace, discover_from_stream


def generate_trace_stream(count: int):
    sites = [
        ("support.route", ["refund", "agent", "faq"], 0.85, True),
        ("agent.tool_select", ["search", "calc", "finish"], 0.70, True),
        ("ticket.priority", ["low", "med", "high"], 0.90, False),
        ("free_text.reply", [f"reply_{i}" for i in range(100)], 0.05, False),
    ]
    for i in range(count):
        site_name, choices, rep_rate, has_verifier = sites[i % len(sites)]
        if (i % 100) < int(rep_rate * 100):
            item_id = i % 15
        else:
            item_id = i
        state = {
            "session_id": f"sess_{i % 500}",
            "item_key": f"item_{item_id}",
            "amount": (i % 20) * 5.0,
        }
        choice = choices[(i % len(choices))]
        outcome = {"quality": 1.0, "verifier": "db"} if has_verifier and (i % 10 != 0) else None
        yield CanonicalTrace(
            timestamp=1700000000.0 + i,
            callsite=site_name,
            state=state,
            choices=choices[:5],
            choice=choice,
            latency_ms=125.0 + (i % 30),
            cost_usd=0.0004,
            model="qwen3.8-27b",
            outcome=outcome,
        )


def run_benchmark():
    print("=" * 60)
    print("LARGE TRACE STREAMING DISCOVERY BENCHMARK")
    print("=" * 60)
    results = {}
    sizes = [10_000, 100_000, 1_000_000]

    for size in sizes:
        gc.collect()
        tracemalloc.start()
        start_time = time.perf_counter()

        stream = generate_trace_stream(size)
        candidates = discover_from_stream(stream)

        elapsed = time.perf_counter() - start_time
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        throughput = round(size / max(0.001, elapsed), 1)
        peak_mb = round(peak / (1024 * 1024), 2)

        print(f"\nScale: {size:,} records")
        print(f"  Elapsed Time : {elapsed:.3f} s")
        print(f"  Throughput   : {throughput:,.1f} records/s")
        print(f"  Peak Memory  : {peak_mb:.2f} MB")
        print(f"  Discovered   : {len(candidates)} sites")
        for c in candidates:
            print(f"    - {c.site_name:18} | Rec: {c.recommendation.upper():12} | Rep: {c.repetition_rate:.1%}")

        results[str(size)] = {
            "records": size,
            "elapsed_seconds": round(elapsed, 4),
            "throughput_rec_per_sec": throughput,
            "peak_memory_mb": peak_mb,
            "candidate_count": len(candidates),
            "candidates": [c.to_dict() for c in candidates],
        }

    out_path = Path(__file__).resolve().parent / "results/large_traces_benchmark.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nSaved benchmark results to {out_path}")


if __name__ == "__main__":
    run_benchmark()
