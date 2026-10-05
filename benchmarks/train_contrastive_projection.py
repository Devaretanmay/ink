"""Train Ink-owned Contrastive Projection Head on top of frozen ModernBERT encoder.

Architecture:
  Encoder (1024-d, frozen)
    -> Linear(1024, 256)
    -> GELU
    -> Linear(256, 128)
    -> L2 Normalization

Objective:
  Triplet Cosine Loss with margin = 0.35, dominated by hard negatives and negations.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as opt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python/ink"))

from ink.internal.model.agent import Agent


class ContrastiveProjectionHead(nn.Module):
    def __init__(self, in_features: int = 1024, hidden_dim: int = 256, out_dim: int = 128):
        super().__init__()
        self.fc1 = nn.Linear(in_features, hidden_dim)
        self.fc2 = nn.Linear(hidden_dim, out_dim)

    def __call__(self, x: mx.array) -> mx.array:
        h = nn.gelu(self.fc1(x))
        z = self.fc2(h)
        norm = mx.linalg.norm(z, axis=-1, keepdims=True)
        return z / mx.maximum(norm, 1e-8)


def get_encoder():
    checkpoint_dir = Path(".ink/models/ink-decision-v1").resolve()
    agent = Agent(str(checkpoint_dir), dtype="float16")

    def encode(text: str) -> np.ndarray:
        ids = agent.tok(text)["input_ids"]
        input_ids = mx.array([ids])
        mask = mx.ones((1, len(ids)), dtype=mx.bool_)
        h = agent.model.encoder(input_ids, mask)
        pooled = np.array(h.mean(axis=1))[0].astype(np.float32)
        norm = np.linalg.norm(pooled)
        if norm > 0:
            pooled /= norm
        return pooled

    return encode


def load_dataset():
    data_path = Path(__file__).resolve().parent / "data" / "contrastive_triplets.jsonl"
    triplets = []
    with open(data_path) as f:
        for line in f:
            if line.strip():
                triplets.append(json.loads(line))
    return triplets


def compute_metrics(head, a_arr, p_arr, n_arr, categories):
    z_a = head(a_arr)
    z_p = head(p_arr)
    z_n = head(n_arr)

    pos_sims = np.array(mx.sum(z_a * z_p, axis=-1)).tolist()
    neg_sims = np.array(mx.sum(z_a * z_n, axis=-1)).tolist()

    negation_indices = [i for i, c in enumerate(categories) if c == "negation"]
    negation_sims = [neg_sims[i] for i in negation_indices]

    margins = [p - n for p, n in zip(pos_sims, neg_sims)]
    
    return {
        "pos_cosine_mean": float(np.mean(pos_sims)),
        "pos_cosine_min": float(np.min(pos_sims)),
        "neg_cosine_mean": float(np.mean(neg_sims)),
        "neg_cosine_max": float(np.max(neg_sims)),
        "negation_cosine_mean": float(np.mean(negation_sims)) if negation_sims else 0.0,
        "negation_cosine_max": float(np.max(negation_sims)) if negation_sims else 0.0,
        "mean_margin": float(np.mean(margins)),
        "hard_negative_violations": int(sum(m <= 0 for m in margins)),
        "triplet_accuracy": float(np.mean([m > 0 for m in margins])),
    }


def train():
    print("=== Step 1: Loading ModernBERT Encoder & Dataset ===")
    encode = get_encoder()
    triplets = load_dataset()
    print(f"Loaded {len(triplets)} contrastive triplets.")

    print("=== Step 2: Caching 1024-d Raw Embeddings ===")
    anchors = [t["anchor"] for t in triplets]
    positives = [t["positive"] for t in triplets]
    negatives = [t["negative"] for t in triplets]
    categories = [t["category"] for t in triplets]

    t0 = time.time()
    a_feats = np.stack([encode(t) for t in anchors])
    p_feats = np.stack([encode(t) for t in positives])
    n_feats = np.stack([encode(t) for t in negatives])
    print(f"Features extracted in {(time.time() - t0):.2f}s. Shape: {a_feats.shape}")

    raw_pos = [float(np.dot(a_feats[i], p_feats[i])) for i in range(len(triplets))]
    raw_neg = [float(np.dot(a_feats[i], n_feats[i])) for i in range(len(triplets))]
    neg_idx = [i for i, c in enumerate(categories) if c == "negation"]
    raw_negation = [raw_neg[i] for i in neg_idx]

    print("\n--- Raw ModernBERT Cosine Baseline ---")
    print(f"  Positive Cosine Mean: {np.mean(raw_pos):.4f}")
    print(f"  Negative Cosine Mean: {np.mean(raw_neg):.4f}")
    print(f"  Negation Cosine Mean: {np.mean(raw_negation):.4f} (Isotropic collapse!)")
    print(f"  Raw Accuracy (Pos > Neg): {np.mean([p > n for p, n in zip(raw_pos, raw_neg)]):.2%}")

    a_arr = mx.array(a_feats)
    p_arr = mx.array(p_feats)
    n_arr = mx.array(n_feats)

    print("\n=== Step 3: Training Contrastive Projection Head ===")
    head = ContrastiveProjectionHead(in_features=1024, hidden_dim=256, out_dim=128)
    optimizer = opt.Adam(learning_rate=3e-3)
    margin = 0.35

    def loss_fn(model, a, p, n):
        za = model(a)
        zp = model(p)
        zn = model(n)
        pos_sim = mx.sum(za * zp, axis=-1)
        neg_sim = mx.sum(za * zn, axis=-1)
        losses = mx.maximum(0.0, neg_sim - pos_sim + margin)
        return mx.mean(losses)

    loss_and_grad = nn.value_and_grad(head, loss_fn)

    epochs = 80
    for epoch in range(1, epochs + 1):
        loss, grads = loss_and_grad(head, a_arr, p_arr, n_arr)
        optimizer.update(head, grads)
        mx.eval(head.parameters(), optimizer.state)

        if epoch % 20 == 0 or epoch == epochs:
            metrics = compute_metrics(head, a_arr, p_arr, n_arr, categories)
            print(
                f"Epoch {epoch:2d}/{epochs} | Loss: {float(loss):.4f} | "
                f"Pos Mean: {metrics['pos_cosine_mean']:.4f} | "
                f"Neg Mean: {metrics['neg_cosine_mean']:.4f} | "
                f"Negation Mean: {metrics['negation_cosine_mean']:.4f} | "
                f"Accuracy: {metrics['triplet_accuracy']:.2%}"
            )

    final_metrics = compute_metrics(head, a_arr, p_arr, n_arr, categories)
    print("\n=== Step 4: Final Evaluation vs Raw Baseline ===")
    print(f"Raw ModernBERT Accuracy: {np.mean([p > n for p, n in zip(raw_pos, raw_neg)]):.2%}")
    print(f"Trained Projection Accuracy: {final_metrics['triplet_accuracy']:.2%}")
    print(f"Raw Negation Cosine: {np.mean(raw_negation):.4f} -> Projected Negation Cosine: {final_metrics['negation_cosine_mean']:.4f}")

    # Save weights
    out_dir = Path(".ink/models").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    weights_path = out_dir / "contrastive_head.npz"

    # Flatten weights to numpy dict
    weights = {
        "fc1_weight": np.array(head.fc1.weight),
        "fc1_bias": np.array(head.fc1.bias),
        "fc2_weight": np.array(head.fc2.weight),
        "fc2_bias": np.array(head.fc2.bias),
    }
    np.savez(weights_path, **weights)
    print(f"Saved projection head weights to: {weights_path}")

    # Save training report
    report = {
        "architecture": {
            "encoder": "ModernBERT-base (frozen, 1024-d)",
            "head": "Linear(1024, 256) -> GELU -> Linear(256, 128) -> L2_norm",
            "parameters": (1024 * 256 + 256) + (256 * 128 + 128),
            "output_dimensions": 128,
        },
        "training": {
            "objective": "Cosine Triplet Loss (margin=0.35)",
            "optimizer": "Adam (lr=3e-3)",
            "epochs": epochs,
            "triplets_count": len(triplets),
            "final_loss": round(float(loss), 4),
        },
        "baseline_raw_encoder": {
            "pos_cosine_mean": round(float(np.mean(raw_pos)), 4),
            "neg_cosine_mean": round(float(np.mean(raw_neg)), 4),
            "negation_cosine_mean": round(float(np.mean(raw_negation)), 4),
            "triplet_accuracy": round(float(np.mean([p > n for p, n in zip(raw_pos, raw_neg)])), 4),
        },
        "contrastive_projection_head": {
            "pos_cosine_mean": round(final_metrics["pos_cosine_mean"], 4),
            "neg_cosine_mean": round(final_metrics["neg_cosine_mean"], 4),
            "negation_cosine_mean": round(final_metrics["negation_cosine_mean"], 4),
            "triplet_accuracy": round(final_metrics["triplet_accuracy"], 4),
            "mean_margin": round(final_metrics["mean_margin"], 4),
        },
    }

    report_path = Path(__file__).resolve().parent / "results" / "contrastive_training_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Saved contrastive training report to: {report_path}")


if __name__ == "__main__":
    train()
