"""SQLite Write Stress & Concurrency Benchmark for Ink Decision JIT.

Measures transactional write throughput, WAL file growth, latency percentiles,
and concurrency contention across 100, 500, 1,000, and 5,000 writes.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import tempfile
import time
import uuid

import numpy as np

sys.path.insert(0, os.path.abspath("python/ink"))

from ink.decision_api import Ink
from ink.internal.contracts import DecisionSite, FallbackResult


def run_write_tier(tier_writes: int, num_workers: int = 4) -> dict:
    print(f"\n--- Stress Testing {tier_writes} writes with {num_workers} concurrent workers ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "stress.db")
        client = Ink(db_path)
        site = DecisionSite("stress.test", {"id": "integer", "payload": "string"}, ("A", "B"))
        client.register(site)

        write_latencies_ms = []
        errors = 0

        def worker_task(worker_id: int, count: int) -> list[float]:
            nonlocal errors
            local_lats = []
            for i in range(count):
                task_id = f"task_w{worker_id}_{i}"
                state = {"id": i, "payload": f"worker_{worker_id}_data_{uuid.uuid4().hex}"}
                t0 = time.perf_counter()
                try:
                    dec = client.decide(
                        site=site.name,
                        state=state,
                        fallback=lambda: FallbackResult(choice="A", model_calls=0),
                        task_id=task_id,
                    )
                    client.record_outcome(
                        dec.decision_id,
                        quality=1.0,
                        verifier="stress_verifier",
                        verifier_version="1",
                        evidence={"worker": worker_id},
                    )
                    lat = (time.perf_counter() - t0) * 1000.0
                    local_lats.append(lat)
                except Exception:
                    errors += 1
            return local_lats

        writes_per_worker = tier_writes // num_workers
        remainder = tier_writes % num_workers

        t_start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = []
            for w in range(num_workers):
                count = writes_per_worker + (1 if w < remainder else 0)
                futures.append(executor.submit(worker_task, w, count))
            for f in concurrent.futures.as_completed(futures):
                write_latencies_ms.extend(f.result())

        wall_time_sec = time.perf_counter() - t_start
        throughput_writes_sec = round(len(write_latencies_ms) / wall_time_sec, 2)

        db_bytes = os.path.getsize(db_path) if os.path.exists(db_path) else 0
        db_size_mb = round(db_bytes / (1024 * 1024), 3)
        wal_path = f"{db_path}-wal"
        wal_bytes = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
        wal_size_mb = round(wal_bytes / (1024 * 1024), 3)

        client.close()

        stats = {
            "tier_target_writes": tier_writes,
            "actual_writes_completed": len(write_latencies_ms),
            "concurrent_workers": num_workers,
            "wall_time_sec": round(wall_time_sec, 3),
            "throughput_writes_per_sec": throughput_writes_sec,
            "errors": errors,
            "error_rate": round(errors / tier_writes, 4) if tier_writes else 0.0,
            "db_size_mb": db_size_mb,
            "wal_size_mb": wal_size_mb,
            "latency": {
                "mean_ms": round(float(np.mean(write_latencies_ms)), 3),
                "p50_ms": round(float(np.percentile(write_latencies_ms, 50)), 3),
                "p95_ms": round(float(np.percentile(write_latencies_ms, 95)), 3),
                "p99_ms": round(float(np.percentile(write_latencies_ms, 99)), 3),
            },
        }
        lat = stats["latency"]
        print(f"Completed {stats['actual_writes_completed']} writes in {stats['wall_time_sec']}s")
        print(
            f"Throughput: {throughput_writes_sec} w/s | "
            f"p50: {lat['p50_ms']}ms | p99: {lat['p99_ms']}ms"
        )
        print(f"DB Size: {db_size_mb} MB | WAL Size: {wal_size_mb} MB | Errors: {errors}")
        return stats


def main():
    print("==================================================================")
    print("Running SQLite WAL Write Stress Benchmark (100, 500, 1000, 5000)")
    print("==================================================================")

    tiers = [100, 500, 1000, 5000]
    tier_results = {}
    for t in tiers:
        tier_results[str(t)] = run_write_tier(t, num_workers=4)

    out_dir = os.path.abspath("benchmarks/results")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "sqlite_stress_report.json")

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sqlite_journal_mode": "WAL",
        "sqlite_synchronous": "FULL",
        "tiers": tier_results,
    }

    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nSaved SQLite write stress report to {out_path}")


if __name__ == "__main__":
    main()
