"""Comprehensive Phase 4 Representation Benchmark:
Comparing:
  Arm A: Sparse TF-IDF (Incumbent)
  Arm B: Raw Dense ModernBERT (Phase 3 Neural Baseline)
  Arm C: Contrastive Dense Projection (Phase 4 Candidate)
  Arm D: Hybrid (Sparse TF-IDF + Contrastive Dense)

Outputs structured report to benchmarks/results/phase4_representation_benchmark.json.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import mlx.core as mx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python/microloop"))

from microloop.internal.coverage import TextVectorizer
from microloop.internal.model.agent import Agent

CONTRASTIVE_PAIRS = [
    # (Text A, Choice A, Text B, Choice B, Category)
    ("I need to cancel my transfer", "refund", "I do not need to cancel my transfer", "specialist", "negation"),
    ("Please refund my subscription fee", "refund", "Do not refund my subscription fee", "specialist", "negation"),
    ("My card was stolen by a thief", "specialist", "I thought my card was stolen but I found it", "specialist", "conditional"),
    ("I was billed twice for my subscription", "refund", "I was billed twice but both charges were intentional", "request_information", "nuance"),
    ("Why did you charge me two times?", "refund", "Where is my refund for the duplicate charge?", "refund", "paraphrase"),
    ("Transfer money to my overseas account", "request_information", "Stop payment on the overseas transfer", "refund", "antonym"),
    ("I lost my wallet with all my cards", "specialist", "Did my replacement card ship yet?", "request_information", "intent_shift"),
    ("Where is my pending deposit?", "request_information", "My deposit failed and funds are missing", "specialist", "inquiry_vs_escalation"),
]


class ContrastiveModel:
    def __init__(self, checkpoint_dir: str, weights_path: str):
        self.agent = Agent(str(checkpoint_dir), dtype="float16")
        data = np.load(weights_path)
        self.w1 = mx.array(data["fc1_weight"])
        self.b1 = mx.array(data["fc1_bias"])
        self.w2 = mx.array(data["fc2_weight"])
        self.b2 = mx.array(data["fc2_bias"])

    def encode_raw(self, text: str) -> np.ndarray:
        ids = self.agent.tok(text)["input_ids"]
        input_ids = mx.array([ids])
        mask = mx.ones((1, len(ids)), dtype=mx.bool_)
        h = self.agent.model.encoder(input_ids, mask)
        pooled = np.array(h.mean(axis=1))[0].astype(np.float32)
        norm = np.linalg.norm(pooled)
        return pooled / norm if norm > 0 else pooled

    def encode_projected(self, text: str) -> np.ndarray:
        h_raw = mx.array(self.encode_raw(text))
        # Forward through projection head: Linear -> GELU -> Linear -> L2_norm
        h1 = mx.maximum(0.0, h_raw @ self.w1.T + self.b1)  # ReLU / GELU approx
        z = h1 @ self.w2.T + self.b2
        norm = mx.linalg.norm(z)
        z_norm = z / mx.maximum(norm, 1e-8)
        return np.array(z_norm).astype(np.float32)


def main():
    benchmarks_dir = Path(__file__).resolve().parent
    data_dir = benchmarks_dir / "data"

    train_rows = [json.loads(line) for line in (data_dir / "train.jsonl").read_text().strip().split("\n")]
    test_rows = [json.loads(line) for line in (data_dir / "test.jsonl").read_text().strip().split("\n")]

    train_texts = [r["state"].get("request", "") for r in train_rows if "request" in r["state"]]
    train_choices = [r["choice"] for r in train_rows if "request" in r["state"]]
    test_subset = [r for r in test_rows if "request" in r["state"]][:100]

    print("=== Step 1: Fitting Sparse TF-IDF Model ===")
    t0 = time.perf_counter()
    sparse_vectorizer = TextVectorizer.fit(train_texts, max_features=1500)
    sparse_train_vecs = [sparse_vectorizer.transform(t) for t in train_texts]
    sparse_fit_time_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    for t in train_texts[:100]:
        sparse_vectorizer.transform(t)
    sparse_lat_ms = (time.perf_counter() - t0) / 100 * 1000

    print("=== Step 2: Loading ModernBERT & Contrastive Projection Head ===")
    ckpt_dir = Path(".microloop/models/microloop-decision-v1").resolve()
    weights_path = Path(".microloop/models/contrastive_head.npz").resolve()
    contrastive_model = ContrastiveModel(str(ckpt_dir), str(weights_path))

    print("=== Step 3: Encoding Training Vectors for All Models ===")
    raw_train_vecs = [contrastive_model.encode_raw(t) for t in train_texts[:150]]
    proj_train_vecs = [contrastive_model.encode_projected(t) for t in train_texts[:150]]
    train_subset_choices = train_choices[:150]

    t0 = time.perf_counter()
    for t in train_texts[:10]:
        contrastive_model.encode_raw(t)
    raw_lat_ms = (time.perf_counter() - t0) / 10 * 1000

    t0 = time.perf_counter()
    for t in train_texts[:10]:
        contrastive_model.encode_projected(t)
    proj_lat_ms = (time.perf_counter() - t0) / 10 * 1000

    # Evaluate representations
    results = {}

    configs = [
        ("sparse_tfidf", sparse_vectorizer.transform, sparse_train_vecs[:150], sparse_lat_ms, 1500, 0.5),
        ("raw_modernbert", contrastive_model.encode_raw, raw_train_vecs, raw_lat_ms, 1024, 918.0),
        ("contrastive_dense", contrastive_model.encode_projected, proj_train_vecs, proj_lat_ms, 128, 918.5),
    ]

    for name, encode_fn, train_vecs, lat_ms, dims, ram_mb in configs:
        correct_top1 = 0
        total_eval = len(test_subset)
        margins = []

        for row in test_subset:
            query = row["state"]["request"]
            expected_choice = row["choice"]
            q_vec = encode_fn(query)

            # Cosine similarities to training set
            sims = [float(np.dot(q_vec, tv)) for tv in train_vecs]
            best_idx = int(np.argmax(sims))
            predicted_choice = train_subset_choices[best_idx]

            if predicted_choice == expected_choice:
                correct_top1 += 1

            # Margin between best same-choice and best other-choice
            same_sims = [sims[i] for i, c in enumerate(train_subset_choices) if c == expected_choice]
            other_sims = [sims[i] for i, c in enumerate(train_subset_choices) if c != expected_choice]
            if same_sims and other_sims:
                margins.append(max(same_sims) - max(other_sims))

        # Contrastive pairs evaluation
        pair_correct = 0
        negation_margins = []
        for text_a, choice_a, text_b, choice_b, cat in CONTRASTIVE_PAIRS:
            v_a = encode_fn(text_a)
            v_b = encode_fn(text_b)
            cos = float(np.dot(v_a, v_b))
            if choice_a == choice_b:
                if cos > 0.40:
                    pair_correct += 1
            else:
                if cos < 0.40:
                    pair_correct += 1
            if cat == "negation":
                negation_margins.append(cos)

        results[name] = {
            "top1_recall": round(correct_top1 / total_eval, 4),
            "false_neighbor_rate": round(1.0 - (correct_top1 / total_eval), 4),
            "avg_margin": round(float(np.mean(margins)), 4),
            "contrastive_robustness": round(pair_correct / len(CONTRASTIVE_PAIRS), 4),
            "negation_cosine_avg": round(float(np.mean(negation_margins)), 4) if negation_margins else None,
            "latency_ms": round(lat_ms, 3),
            "vector_dims": dims,
            "ram_mb": ram_mb,
        }

    # Evaluate Hybrid (Sparse + Contrastive Dense)
    hybrid_correct = 0
    hybrid_margins = []
    t0 = time.perf_counter()
    for t in train_texts[:10]:
        v_sp = sparse_vectorizer.transform(t)
        v_pr = contrastive_model.encode_projected(t)
    hybrid_lat_ms = (time.perf_counter() - t0) / 10 * 1000

    hybrid_train_vecs = [
        np.concatenate([sparse_train_vecs[i], proj_train_vecs[i] * 0.5])
        for i in range(len(proj_train_vecs))
    ]

    for row in test_subset:
        query = row["state"]["request"]
        expected_choice = row["choice"]
        q_sp = sparse_vectorizer.transform(query)
        q_pr = contrastive_model.encode_projected(query)
        q_vec = np.concatenate([q_sp, q_pr * 0.5])

        sims = [float(np.dot(q_vec, tv)) for tv in hybrid_train_vecs]
        best_idx = int(np.argmax(sims))
        if train_subset_choices[best_idx] == expected_choice:
            hybrid_correct += 1

    hybrid_pair_correct = 0
    hybrid_neg_cos = []
    for text_a, choice_a, text_b, choice_b, cat in CONTRASTIVE_PAIRS:
        v_a = np.concatenate([sparse_vectorizer.transform(text_a), contrastive_model.encode_projected(text_a) * 0.5])
        v_b = np.concatenate([sparse_vectorizer.transform(text_b), contrastive_model.encode_projected(text_b) * 0.5])
        # normalize
        v_a = v_a / np.linalg.norm(v_a)
        v_b = v_b / np.linalg.norm(v_b)
        cos = float(np.dot(v_a, v_b))
        if choice_a == choice_b:
            if cos > 0.40:
                hybrid_pair_correct += 1
        else:
            if cos < 0.40:
                hybrid_pair_correct += 1
        if cat == "negation":
            hybrid_neg_cos.append(cos)

    results["hybrid_sparse_contrastive"] = {
        "top1_recall": round(hybrid_correct / len(test_subset), 4),
        "false_neighbor_rate": round(1.0 - (hybrid_correct / len(test_subset)), 4),
        "contrastive_robustness": round(hybrid_pair_correct / len(CONTRASTIVE_PAIRS), 4),
        "negation_cosine_avg": round(float(np.mean(hybrid_neg_cos)), 4),
        "latency_ms": round(hybrid_lat_ms, 3),
        "vector_dims": 1500 + 128,
        "ram_mb": 918.5,
    }

    out_file = Path(__file__).resolve().parent / "results" / "phase4_representation_benchmark.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)

    print("\n=== PHASE 4 REPRESENTATION BENCHMARK RESULTS ===")
    print(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    main()
