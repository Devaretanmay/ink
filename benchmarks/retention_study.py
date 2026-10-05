"""Ink Decision JIT Storage Retention Study."""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python" / "ink"))

from ink import DecisionSite, Ink, Outcome, PromotionRequirements
from ink.internal.contracts import canonical


def run_retention_study():
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_file = results_dir / "retention_study.json"

    site = DecisionSite("retention.ticket", {"text": "string"}, ("approve", "reject"))
    req = PromotionRequirements(
        min_samples=10,
        min_quality=0.5,
        min_confidence=0.5,
        max_degradation=0.8,
        comparison_rate=0.20,
        min_region_samples=5,
        evaluation_window=1000,
    )

    def verify_fn(state, choice):
        exp = "approve" if "approve" in state["text"] else "reject"
        return Outcome(float(choice == exp), "retention_verifier", "1", {"expected": exp})

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "retention.db"
        with Ink(db_path) as client:
            # Seed initial site and calibrate/qualify
            for i in range(200):
                exp = "approve" if i % 2 == 0 else "reject"
                st = {"text": f"approve tx {i % 4}" if exp == "approve" else f"reject err {i % 4}"}
                res = client.decide(site=site, state=st, fallback=lambda exp=exp: exp)
                client.record_outcome(
                    res.decision_id,
                    quality=1.0,
                    verifier="retention_verifier",
                    verifier_version="1",
                    evidence={"ok": True},
                )

            client.compile(site, engine="exact")
            client.calibrate(site, verifier=verify_fn, requirements=req)
            for i in range(100):
                exp = "approve" if i % 2 == 0 else "reject"
                st = {"text": f"approve tx {i % 4}" if exp == "approve" else f"reject err {i % 4}"}
                res = client.decide(site=site, state=st, fallback=lambda exp=exp: exp)
                client.record_outcome(
                    res.decision_id,
                    quality=1.0,
                    verifier="retention_verifier",
                    verifier_version="1",
                    evidence={"ok": True},
                )
            client.evaluate(site, verifier=verify_fn)

            checkpoints = [10000, 25000, 50000]
            checkpoint_data = {}
            insert_latencies = []
            sampled_ids = []

            current_count = 300
            for target in checkpoints:
                to_add = target - current_count
                start_batch = time.perf_counter()
                for i in range(to_add):
                    idx = current_count + i
                    exp = "approve" if idx % 2 == 0 else "reject"
                    tag = f"approve tx {idx % 4}" if exp == "approve" else f"reject {idx % 4}"
                    st = {"text": tag}
                    t0 = time.perf_counter()
                    res = client.decide(site=site, state=st, fallback=lambda: "approve")
                    t1 = time.perf_counter()
                    insert_latencies.append((t1 - t0) * 1000.0)

                    if idx % 100 == 0:
                        sampled_ids.append(res.decision_id)

                    client.record_outcome(
                        res.decision_id,
                        quality=1.0,
                        verifier="retention_verifier",
                        verifier_version="1",
                        evidence={"ok": True},
                    )

                current_count = target
                batch_duration = time.perf_counter() - start_batch
                file_size_bytes = os.path.getsize(db_path)
                wal_path = Path(f"{db_path}-wal")
                wal_size_bytes = os.path.getsize(wal_path) if wal_path.exists() else 0

                query_times = []
                for s_id in sampled_ids[-50:]:
                    tq0 = time.perf_counter()
                    client.receipt(s_id)
                    tq1 = time.perf_counter()
                    query_times.append((tq1 - tq0) * 1000.0)

                b_per_dec = round(file_size_bytes / target, 1)
                checkpoint_data[f"{target}_rows"] = {
                    "total_rows": target,
                    "db_size_mb": round(file_size_bytes / (1024 * 1024), 2),
                    "wal_size_mb": round(wal_size_bytes / (1024 * 1024), 2),
                    "bytes_per_decision": b_per_dec,
                    "decide_latency_p50_ms": round(sorted(insert_latencies[-1000:])[500], 4),
                    "decide_latency_p99_ms": round(sorted(insert_latencies[-1000:])[990], 4),
                    "receipt_lookup_p50_ms": round(sorted(query_times)[len(query_times) // 2], 4),
                    "throughput_decisions_per_sec": round(to_add / batch_duration, 1),
                }

            pre_prune_size = os.path.getsize(db_path)
            t_prune_0 = time.perf_counter()
            with client.store.transaction() as db:
                db.execute(
                    "DELETE FROM decisions "
                    "WHERE created < (SELECT max(created) FROM decisions) - 3600"
                )
            t_prune_1 = time.perf_counter()
            prune_duration_ms = (t_prune_1 - t_prune_0) * 1000.0

            t_vac_0 = time.perf_counter()
            client.store.conn.execute("VACUUM")
            t_vac_1 = time.perf_counter()
            vacuum_duration_ms = (t_vac_1 - t_vac_0) * 1000.0
            post_vacuum_size = os.path.getsize(db_path)

            b_rate = checkpoint_data["50000_rows"]["bytes_per_decision"]
            study_summary = {
                "checkpoints": checkpoint_data,
                "retention_maintenance": {
                    "pre_prune_size_mb": round(pre_prune_size / (1024 * 1024), 2),
                    "post_vacuum_size_mb": round(post_vacuum_size / (1024 * 1024), 2),
                    "reclaimed_space_ratio": round(1.0 - (post_vacuum_size / pre_prune_size), 3),
                    "prune_time_ms": round(prune_duration_ms, 2),
                    "vacuum_time_ms": round(vacuum_duration_ms, 2),
                },
                "extrapolations": {
                    "1M_decisions_db_size_mb": round((b_rate * 1000000) / (1024 * 1024), 1),
                    "10M_decisions_db_size_mb": round((b_rate * 10000000) / (1024 * 1024), 1),
                    "retention_recommendation": (
                        "Prune decisions older than evaluation window (1,000-10,000 decisions); "
                        "execute VACUUM during low-traffic maintenance window."
                    ),
                },
            }

            out_file.write_text(canonical(study_summary), encoding="utf-8")
            print(f"Retention study complete. Saved to {out_file}")
            print(json.dumps(study_summary, indent=2))


if __name__ == "__main__":
    run_retention_study()
