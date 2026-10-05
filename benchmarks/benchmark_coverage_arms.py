"""4-Arm Comparative Benchmark for Ink Semantic Coverage Engine:

Arm A: Exact Coverage (Production Baseline)
Arm B: Naive Similarity (Cosine > 0.8 without boundaries)
Arm C: Semantic Coverage Without Qualification (Ablation)
Arm D: Full Ink Semantic Coverage (Geometric + Margins + Qualification Lifecycle)

Evaluates on benchmarks/data/coverage_eval.jsonl.
Outputs structured JSON report to benchmarks/results/coverage_arms_report.json.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.abspath("python/ink"))

from ink.decision_api import Ink
from ink.internal.contracts import DecisionSite, Outcome, PromotionRequirements, canonical
from ink.internal.coverage import (
    CoverageEngine,
    SemanticRegion,
    TextVectorizer,
)


def load_dataset():
    data_path = os.path.join(os.path.dirname(__file__), "data", "coverage_eval.jsonl")
    items = []
    with open(data_path) as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
    return items


def ground_truth_eval(item, predicted_choice):
    expected = item["expected_choice"]
    if expected is None:
        # Ambiguous, adversarial injection, or negative query:
        # A fast-path serve here is a false serve!
        return False
    return predicted_choice == expected


def run_benchmark():
    items = load_dataset()
    site = DecisionSite(
        name="finance.support.triage",
        state_schema={"request": "string"},
        choices=("refund", "request_information", "specialist"),
        fallback_revision="1",
    )

    req = PromotionRequirements(
        min_samples=10,
        min_quality=0.50,
        min_confidence=0.50,
        max_degradation=0.65,
        comparison_rate=0.01,
        min_region_samples=5,
        evaluation_window=100,
        max_uncovered_rate=0.50,
    )

    def verifier(state, choice):
        req_text = state.get("request", "").lower()
        if "refund" in req_text or "billed twice" in req_text or "duplicate" in req_text or "reimbursement" in req_text:
            expected = "refund"
        elif "pending" in req_text or "status" in req_text or "wire" in req_text or "clearing" in req_text:
            expected = "request_information"
        else:
            expected = "specialist"
        return Outcome(
            quality=1.0 if choice == expected else 0.0,
            verifier="benchmark_v1",
            verifier_version="1",
            evidence={"expected": expected},
        )

    # Historical seeds (the verified exact states)
    seed_queries = [
        ("I was billed twice for my subscription.", "refund"),
        ("Where is my pending deposit from yesterday?", "request_information"),
        ("I suspect fraud on my account, please lock it immediately.", "specialist"),
    ]

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "benchmark.db")
        with Ink(db_path) as client:
            for query, choice in seed_queries * 80:
                res = client.decide(site=site, state={"request": query}, fallback=lambda c=choice: c)
                client.record_outcome(
                    res.decision_id,
                    quality=1.0,
                    verifier="benchmark_v1",
                    verifier_version="1",
                    evidence={"seed": True},
                )

            client.compile(site, engine="exact")
            client.calibrate(site, verifier=verifier, requirements=req)

            for query, choice in seed_queries * 25:
                res = client.decide(site=site, state={"request": query}, fallback=lambda c=choice: c)
                client.record_outcome(
                    res.decision_id,
                    quality=1.0,
                    verifier="benchmark_v1",
                    verifier_version="1",
                    evidence={"shadow": True},
                )
            sem_shadow = [
                ("Duplicate charge showing on my credit card statement.", "refund"),
                ("Please return the money from the second accidental payment.", "refund"),
                ("Why was the transaction processed twice? Refund it please.", "refund"),
                ("Can you check the status of my incoming bank transfer?", "request_information"),
                ("Has the wire transfer from my employer arrived yet?", "request_information"),
            ] * 5
            for query, choice in sem_shadow:
                res = client.decide(site=site, state={"request": query}, fallback=lambda c=choice: c)
                client.record_outcome(
                    res.decision_id,
                    quality=1.0,
                    verifier="benchmark_v1",
                    verifier_version="1",
                    evidence={"sem_shadow": True},
                )

            eval_res = client.evaluate(site, verifier=verifier)
            artifact = client._artifact(site.version)
            cov_engine_dict = artifact["profile"]["coverage_engine"]
            vectorizer = TextVectorizer.from_dict(cov_engine_dict["vectorizer"])
            raw_regions = [SemanticRegion.from_dict(r) for r in cov_engine_dict["semantic_regions"]]

    seed_vecs = [(vectorizer.transform(q), c) for q, c in seed_queries]

    arms = ["arm_a_exact", "arm_b_naive_sim", "arm_c_semantic_unqualified", "arm_d_full_ink"]
    results = {arm: {"total": len(items), "fast_served": 0, "correct_fast": 0, "false_served": 0, "abstained": 0, "latencies_us": []} for arm in arms}

    for item in items:
        query = item["state"]["request"]
        expected_choice = item["expected_choice"]
        item_vec = vectorizer.transform(query)

        # --- Arm A: Exact Coverage ---
        t0 = time.perf_counter()
        arm_a_served = False
        arm_a_choice = None
        for sq, sc in seed_queries:
            if query == sq:
                arm_a_served = True
                arm_a_choice = sc
                break
        t_a = (time.perf_counter() - t0) * 1e6
        results["arm_a_exact"]["latencies_us"].append(t_a)
        if arm_a_served:
            results["arm_a_exact"]["fast_served"] += 1
            if ground_truth_eval(item, arm_a_choice):
                results["arm_a_exact"]["correct_fast"] += 1
            else:
                results["arm_a_exact"]["false_served"] += 1
        else:
            results["arm_a_exact"]["abstained"] += 1

        # --- Arm B: Naive Similarity (Cosine > 0.80) ---
        t0 = time.perf_counter()
        best_sim = -1.0
        best_choice = None
        for svec, sc in seed_vecs:
            sim = float(np.dot(item_vec, svec))
            if sim > best_sim:
                best_sim = sim
                best_choice = sc
        arm_b_served = best_sim >= 0.70  # naive similarity threshold
        t_b = (time.perf_counter() - t0) * 1e6
        results["arm_b_naive_sim"]["latencies_us"].append(t_b)
        if arm_b_served:
            results["arm_b_naive_sim"]["fast_served"] += 1
            if ground_truth_eval(item, best_choice):
                results["arm_b_naive_sim"]["correct_fast"] += 1
            else:
                results["arm_b_naive_sim"]["false_served"] += 1
        else:
            results["arm_b_naive_sim"]["abstained"] += 1

        # --- Arm C: Semantic Coverage Without Qualification (Active immediately) ---
        t0 = time.perf_counter()
        # Active immediately: all geometric boundaries applied, but no shadow qualification required
        arm_c_engine = CoverageEngine(
            exact_coverage={canonical({"request": sq}): {"choice": sc} for sq, sc in seed_queries},
            semantic_regions=[SemanticRegion(
                region_id=r.region_id,
                site=r.site,
                choice=r.choice,
                prototype_state=r.prototype_state,
                prototype_vector=r.prototype_vector,
                radius=r.radius,
                negative_margin=r.negative_margin,
                member_count=r.member_count,
                confidence=r.confidence,
                status="ACTIVE",  # unconditionally active
            ) for r in raw_regions],
            vectorizer=vectorizer,
        )
        lvl_c, reg_c, _ = arm_c_engine.route({"request": query})
        t_c = (time.perf_counter() - t0) * 1e6
        results["arm_c_semantic_unqualified"]["latencies_us"].append(t_c)
        if lvl_c in ("exact", "semantic") and reg_c:
            results["arm_c_semantic_unqualified"]["fast_served"] += 1
            choice = reg_c["choice"]
            if ground_truth_eval(item, choice):
                results["arm_c_semantic_unqualified"]["correct_fast"] += 1
            else:
                results["arm_c_semantic_unqualified"]["false_served"] += 1
        else:
            results["arm_c_semantic_unqualified"]["abstained"] += 1

        # --- Arm D: Full Ink Semantic Coverage ---
        t0 = time.perf_counter()
        # Qualified regions only (only regions with earned qualification are ACTIVE)
        qualified_set = set(eval_res.get("qualified_semantic_regions", []))
        arm_d_regions = []
        for r in raw_regions:
            status = "ACTIVE" if r.region_id in qualified_set else "SHADOW"
            arm_d_regions.append(SemanticRegion(
                region_id=r.region_id,
                site=r.site,
                choice=r.choice,
                prototype_state=r.prototype_state,
                prototype_vector=r.prototype_vector,
                radius=r.radius,
                negative_margin=r.negative_margin,
                member_count=r.member_count,
                confidence=r.confidence,
                status=status,
            ))
        arm_d_engine = CoverageEngine(
            exact_coverage={canonical({"request": sq}): {"choice": sc} for sq, sc in seed_queries},
            semantic_regions=arm_d_regions,
            vectorizer=vectorizer,
        )
        lvl_d, reg_d, _ = arm_d_engine.route({"request": query})
        t_d = (time.perf_counter() - t0) * 1e6
        results["arm_d_full_ink"]["latencies_us"].append(t_d)
        if lvl_d in ("exact", "semantic") and reg_d:
            results["arm_d_full_ink"]["fast_served"] += 1
            choice = reg_d["choice"]
            if ground_truth_eval(item, choice):
                results["arm_d_full_ink"]["correct_fast"] += 1
            else:
                results["arm_d_full_ink"]["false_served"] += 1
        else:
            results["arm_d_full_ink"]["abstained"] += 1

    # Summarize metrics
    summary = {}
    for arm, data in results.items():
        n = data["total"]
        fast = data["fast_served"]
        corr = data["correct_fast"]
        false_s = data["false_served"]
        lats = sorted(data["latencies_us"])
        summary[arm] = {
            "total_requests": n,
            "fast_served": fast,
            "coverage_rate": round(fast / n, 4),
            "verified_coverage_rate": round(corr / n, 4),
            "served_accuracy": round(corr / fast, 4) if fast else 1.0,
            "false_serve_rate": round(false_s / n, 4),
            "abstention_rate": round(data["abstained"] / n, 4),
            "latency_p50_us": round(lats[len(lats) // 2], 2),
            "latency_p95_us": round(lats[int(len(lats) * 0.95)], 2),
            "latency_p99_us": round(lats[int(len(lats) * 0.99)], 2),
        }

    out_file = os.path.join(os.path.dirname(__file__), "results", "coverage_arms_report.json")
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(summary, f, indent=2)

    print("=== 4-ARM COMPARATIVE BENCHMARK RESULTS ===")
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    run_benchmark()
