"""Benchmark Decision JIT scaling across 10, 100, and 500 sites."""

import json
import resource
import shutil
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements

REQ = PromotionRequirements(10, 0.5, 0.5, 0.6, 0.25, 5, 100)


def get_rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return usage / (1024 * 1024) if sys.platform == "darwin" else usage / 1024


def make_site(index: int) -> DecisionSite:
    return DecisionSite(
        f"fleet.site.{index:04d}",
        {"feature": "integer"},
        ("action_a", "action_b"),
    )


def verify_site(state, choice):
    exp = "action_a" if state["feature"] % 2 == 0 else "action_b"
    return Outcome(float(choice == exp), "scale_verifier", "1", {"expected": exp})


def benchmark_fleet(tier: int, temp_dir: Path) -> dict:
    db_path = temp_dir / f"fleet_{tier}.db"
    if db_path.exists():
        db_path.unlink()

    sites = [make_site(i) for i in range(tier)]
    t_start = time.perf_counter()

    with Ink(db_path) as client:
        # 1. Register and seed sites
        for site in sites:
            # Seed 30 decisions per site
            for j in range(30):
                st = {"feature": j}
                exp = "action_a" if j % 2 == 0 else "action_b"
                res = client.decide(
                    site=site,
                    state=st,
                    task_id=f"obs-{site.name}-{j}",
                    fallback=lambda exp=exp: FallbackResult(exp, model_calls=1),
                )
                out = verify_site(st, res.choice)
                client.record_outcome(res.decision_id, **asdict(out))

        setup_time = time.perf_counter() - t_start

        # 2. Measure routing latency across random sites
        latencies_ms = []
        rng = np.random.default_rng(42)
        random_site_indices = rng.integers(0, tier, size=200)

        for idx in random_site_indices:
            site = sites[idx]
            val = int(rng.integers(0, 100))
            t0 = time.perf_counter()
            client.decide(
                site=site,
                state={"feature": val},
                fallback=lambda val=val: "action_a" if val % 2 == 0 else "action_b",
            )
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)

        p50_latency = float(np.percentile(latencies_ms, 50))
        p99_latency = float(np.percentile(latencies_ms, 99))
        mean_latency = float(np.mean(latencies_ms))

        # 3. Measure maintenance execution time and bounding
        t_maint_start = time.perf_counter()
        # Compile up to min(20, tier) sites with requirements
        maint_res = client.maintenance(
            max_sites=min(50, tier),
            requirements=REQ,
            engine="exact",
        )
        maint_time = time.perf_counter() - t_maint_start
        compiled_count = sum(1 for v in maint_res.values() if "compiled" in v)

        # 4. Measure fleet health query time
        t_health_start = time.perf_counter()
        health = client.fleet_health()
        health_query_time = time.perf_counter() - t_health_start

    db_size_bytes = db_path.stat().st_size
    wal_path = Path(str(db_path) + "-wal")
    if wal_path.exists():
        db_size_bytes += wal_path.stat().st_size

    rss_mb = get_rss_mb()

    return {
        "tier_sites": tier,
        "total_observations": tier * 30 + 200,
        "setup_time_sec": round(setup_time, 3),
        "routing_latency_mean_ms": round(mean_latency, 4),
        "routing_latency_p50_ms": round(p50_latency, 4),
        "routing_latency_p99_ms": round(p99_latency, 4),
        "maintenance_time_sec": round(maint_time, 4),
        "maintenance_sites_processed": min(50, tier),
        "compiled_sites": compiled_count,
        "compilation_throughput_sites_per_sec": round(
            compiled_count / max(maint_time, 0.001), 2
        ),
        "health_query_time_sec": round(health_query_time, 4),
        "db_size_mb": round(db_size_bytes / (1024 * 1024), 3),
        "peak_rss_mb": round(rss_mb, 1),
        "fleet_coverage": health["summary"]["fleet_coverage"],
    }


def main():
    print("Running Fleet Scale Benchmark (10, 100, 500 sites)...")
    temp_dir = Path(tempfile.mkdtemp(prefix="ink_scale_"))
    results = {"tiers": {}}

    try:
        for tier in (10, 100, 500):
            print(f"\n--- Benchmarking Tier: {tier} sites ---")
            res = benchmark_fleet(tier, temp_dir)
            results["tiers"][str(tier)] = res
            print(f"  Routing p50 Latency: {res['routing_latency_p50_ms']:.4f} ms")
            print(f"  Routing p99 Latency: {res['routing_latency_p99_ms']:.4f} ms")
            print(
                f"  Maintenance Time ({res['maintenance_sites_processed']} sites): "
                f"{res['maintenance_time_sec']:.3f} s"
            )
            print(f"  DB Size: {res['db_size_mb']:.2f} MB")
            print(f"  Peak RSS: {res['peak_rss_mb']:.1f} MB")

        out_path = Path("benchmarks/results/fleet_scale_benchmark.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, indent=2))
        print(f"\nBenchmark completed successfully! Results saved to {out_path}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
