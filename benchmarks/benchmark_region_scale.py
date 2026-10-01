import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "python" / "microloop"))

from microloop.internal.contracts import canonical
from microloop.internal.coverage import CoverageEngine, SemanticRegion


def create_mock_regions(count: int, dim: int = 64) -> list[SemanticRegion]:
    regions = []
    # Seed reproducible random unit vectors
    rng = np.random.default_rng(42)
    raw_vecs = rng.normal(0, 1, size=(count, dim)).astype(np.float32)
    norms = np.linalg.norm(raw_vecs, axis=1, keepdims=True)
    unit_vecs = raw_vecs / np.maximum(norms, 1e-9)

    choices = ("approve", "reject", "escalate")
    for i in range(count):
        reg = SemanticRegion(
            region_id=f"reg-{i:06d}",
            site="scale.site",
            choice=choices[i % len(choices)],
            prototype_state={"id": i},
            prototype_vector=unit_vecs[i].tolist(),
            radius=0.35,
            negative_margin=0.50,
            member_count=20,
            confidence=0.92,
            status="ACTIVE",
        )
        regions.append(reg)
    return regions


def benchmark_loop_lookup(engine: CoverageEngine, query_vec: np.ndarray) -> list[tuple[int, float]]:
    matches = []
    for i, reg in enumerate(engine.semantic_regions):
        proto = np.array(reg.prototype_vector, dtype=np.float32)
        cos_sim = float(np.dot(query_vec, proto))
        dist = max(0.0, 1.0 - min(1.0, cos_sim))
        if dist <= reg.radius:
            matches.append((i, dist))
    return matches


class VectorizedCoverageEngine:
    def __init__(self, semantic_regions: list[SemanticRegion]):
        self.semantic_regions = semantic_regions
        if semantic_regions:
            self.matrix = np.array(
                [r.prototype_vector for r in semantic_regions], dtype=np.float32
            )
            self.radii = np.array(
                [r.radius for r in semantic_regions], dtype=np.float32
            )
        else:
            self.matrix = np.empty((0, 0), dtype=np.float32)
            self.radii = np.empty((0,), dtype=np.float32)

    def lookup(self, query_vec: np.ndarray) -> list[tuple[int, float]]:
        if len(self.semantic_regions) == 0:
            return []
        # Single vectorized BLAS GEMV product
        cos_sims = self.matrix @ query_vec
        dists = np.clip(1.0 - cos_sims, 0.0, 2.0)
        mask = dists <= self.radii
        matching_indices = np.where(mask)[0]
        return [(idx, float(dists[idx])) for idx in matching_indices]

    def lookup_chunked(
        self, query_vec: np.ndarray, chunk_size: int = 10000
    ) -> list[tuple[int, float]]:
        if len(self.semantic_regions) == 0:
            return []
        matches = []
        n = len(self.semantic_regions)
        for start in range(0, n, chunk_size):
            end = min(start + chunk_size, n)
            sub_mat = self.matrix[start:end]
            sub_radii = self.radii[start:end]
            cos_sims = sub_mat @ query_vec
            dists = np.clip(1.0 - cos_sims, 0.0, 2.0)
            sub_mask = dists <= sub_radii
            for idx in np.where(sub_mask)[0]:
                matches.append((start + idx, float(dists[idx])))
        return matches


def run_scale_benchmark():
    scale_counts = [100, 1000, 10000, 100000]
    dim = 64
    rng = np.random.default_rng(123)

    results = {
        "scale_study": {},
        "vectorization_comparison": {},
    }

    print("=== Microloop Phase 6 Large-Scale Semantic Region Benchmark ===")

    for count in scale_counts:
        regions = create_mock_regions(count, dim=dim)

        engine_loop = CoverageEngine(semantic_regions=regions)
        t_mat_0 = time.perf_counter()
        engine_vec = VectorizedCoverageEngine(semantic_regions=regions)
        t_mat = (time.perf_counter() - t_mat_0) * 1000.0

        # Create 100 test query vectors
        raw_queries = rng.normal(0, 1, size=(100, dim)).astype(np.float32)
        queries = raw_queries / np.linalg.norm(raw_queries, axis=1, keepdims=True)

        # 1. Benchmark Vectorized GEMV Lookup
        vec_latencies = []
        for q in queries:
            t0 = time.perf_counter()
            _ = engine_vec.lookup(q)
            t1 = time.perf_counter()
            vec_latencies.append((t1 - t0) * 1000.0)

        # 2. Benchmark Loop Lookup (only up to 10k to prevent excessive test time at 100k)
        loop_latencies = []
        if count <= 10000:
            for q in queries[:20]:
                t0 = time.perf_counter()
                _ = benchmark_loop_lookup(engine_loop, q)
                t1 = time.perf_counter()
                loop_latencies.append((t1 - t0) * 1000.0)

        # 3. Benchmark Chunked Vectorized Lookup
        chunked_latencies = []
        for q in queries:
            t0 = time.perf_counter()
            _ = engine_vec.lookup_chunked(q, chunk_size=10000)
            t1 = time.perf_counter()
            chunked_latencies.append((t1 - t0) * 1000.0)

        # Profile serialization size
        serialized_profile = canonical([r.to_dict() for r in regions[:min(count, 1000)]])
        est_serialized_mb = (len(serialized_profile) / min(count, 1000) * count) / (1024 * 1024)
        matrix_mem_kb = (engine_vec.matrix.nbytes + engine_vec.radii.nbytes) / 1024.0

        vec_p50 = float(np.percentile(vec_latencies, 50))
        vec_p95 = float(np.percentile(vec_latencies, 95))
        vec_p99 = float(np.percentile(vec_latencies, 99))
        loop_p50 = float(np.percentile(loop_latencies, 50)) if loop_latencies else None

        results["scale_study"][f"{count}_regions"] = {
            "region_count": count,
            "vector_dimension": dim,
            "matrix_memory_kb": round(matrix_mem_kb, 1),
            "estimated_json_size_mb": round(est_serialized_mb, 2),
            "matrix_build_time_ms": round(t_mat, 2),
            "vectorized_p50_ms": round(vec_p50, 4),
            "vectorized_p95_ms": round(vec_p95, 4),
            "vectorized_p99_ms": round(vec_p99, 4),
            "loop_p50_ms": round(loop_p50, 4) if loop_p50 else "N/A (>10K timeout)",
            "speedup_vs_loop": round(loop_p50 / vec_p50, 1) if loop_p50 else "N/A",
        }

        print(
            f"[{count:6d} Regions] Vectorized p50: {vec_p50:.4f} ms | "
            f"p99: {vec_p99:.4f} ms | Matrix RAM: {matrix_mem_kb:.1f} KB | "
            f"Loop p50: {loop_p50 if loop_p50 else 'N/A'}"
        )

    out_file = Path(__file__).parent / "results" / "region_scale_benchmark.json"
    out_file.write_text(canonical(results), encoding="utf-8")
    print(f"\nSaved benchmark results to {out_file}")


if __name__ == "__main__":
    run_scale_benchmark()
