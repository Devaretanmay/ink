"""Benchmark sparse (TF-IDF), neural (ModernBERT), and hybrid representations for semantic coverage."""

from __future__ import annotations

import json
import math
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python/microloop"))

from microloop.internal.model.agent import Agent

# Contrastive pairs specifically designed to test semantic boundary robustness:
CONTRASTIVE_PAIRS = [
    # (Text A, Choice A, Text B, Choice B, Notes)
    ("I need to cancel my transfer", "refund", "I do not need to cancel my transfer", "specialist", "Direct negation"),
    ("My card was stolen by a thief", "specialist", "I thought my card was stolen but I found it", "specialist", "Conditional resolution"),
    ("I was billed twice for my subscription", "refund", "I was billed twice but both charges were intentional", "request_information", "Semantic nuance"),
    ("Why did you charge me two times?", "refund", "Where is my refund for the duplicate charge?", "refund", "Paraphrase same intent"),
    ("Transfer money to my overseas account", "request_information", "Stop payment on the overseas transfer", "refund", "Antonym action"),
    ("I lost my wallet with all my cards", "specialist", "Did my replacement card ship yet?", "request_information", "Pre vs post event"),
    ("Where is my pending deposit?", "request_information", "My deposit failed and funds are missing", "specialist", "Inquiry vs escalation"),
    ("I want to close my account immediately", "specialist", "How do I update my account address?", "request_information", "Termination vs maintenance"),
]

class SparseTFIDF:
    def __init__(self, max_features=1000):
        self.max_features = max_features
        self.vocab = {}
        self.idf = {}
        
    def fit(self, texts):
        df = Counter()
        n = len(texts)
        for t in texts:
            words = set(self._tokenize(t))
            for w in words:
                df[w] += 1
        # Select top features
        top = [w for w, _ in df.most_common(self.max_features)]
        self.vocab = {w: i for i, w in enumerate(top)}
        self.idf = {w: math.log((n + 1) / (df[w] + 1)) + 1.0 for w in top}
        
    def _tokenize(self, text):
        words = text.lower().replace("?", " ").replace("!", " ").replace(".", " ").replace(",", " ").split()
        bigrams = [f"{words[i]}_{words[i+1]}" for i in range(len(words)-1)]
        return words + bigrams
        
    def transform(self, text):
        vec = np.zeros(len(self.vocab), dtype=np.float32)
        words = self._tokenize(text)
        counts = Counter(words)
        for w, c in counts.items():
            if w in self.vocab:
                idx = self.vocab[w]
                vec[idx] = c * self.idf[w]
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

def get_neural_encoder(checkpoint_dir):
    import mlx.core as mx
    agent = Agent(str(checkpoint_dir), dtype="float16")
    
    def encode(text):
        ids = agent.tok(text)["input_ids"]
        input_ids = mx.array([ids])
        mask = mx.ones((1, len(ids)), dtype=mx.bool_)
        h = agent.model.encoder(input_ids, mask)
        pooled = np.array(h.mean(axis=1))[0].astype(np.float32)
        norm = np.linalg.norm(pooled)
        if norm > 0:
            pooled /= norm
        return pooled
        
    return encode, agent

def cosine_sim(a, b):
    return float(np.dot(a, b))

def main():
    benchmarks_dir = Path(__file__).resolve().parent
    data_dir = benchmarks_dir / "data"
    train_rows = [json.loads(line) for line in (data_dir / "train.jsonl").read_text().strip().split("\n")]
    test_rows = [json.loads(line) for line in (data_dir / "test.jsonl").read_text().strip().split("\n")]
    
    train_texts = [r["state"].get("request", "") for r in train_rows if "request" in r["state"]]
    test_subset = [r for r in test_rows if "request" in r["state"]][:100]
    
    print("=" * 60)
    print("SEMANTIC REPRESENTATION COMPARISON (SPARSE vs NEURAL vs HYBRID)")
    print("=" * 60)
    
    t0 = time.time()
    sparse_model = SparseTFIDF(max_features=1500)
    sparse_model.fit(train_texts)
    sparse_train_vecs = [sparse_model.transform(t) for t in train_texts]
    sparse_fit_time = (time.time() - t0) * 1000
    
    t0 = time.time()
    for t in train_texts[:100]:
        sparse_model.transform(t)
    sparse_latency_ms = (time.time() - t0) / 100 * 1000
    
    checkpoint_dir = Path(".microloop/models/microloop-decision-v1").resolve()
    t0 = time.time()
    neural_encode, agent = get_neural_encoder(checkpoint_dir)
    neural_load_time = (time.time() - t0) * 1000
    
    t0 = time.time()
    for t in train_texts[:50]:
        neural_encode(t)
    neural_latency_ms = (time.time() - t0) / 50 * 1000
    
    neural_train_vecs = [neural_encode(t) for t in train_texts[:300]]
    train_choices = [train_rows[i]["choice"] for i in range(len(neural_train_vecs))]
    sparse_ref_vecs = sparse_train_vecs[:300]
    
    def eval_retrieval(kind):
        correct_same_choice = 0
        margins = []
        neg_separation_failures = 0  # when nearest diff choice is closer than nearest same choice
        
        for r in test_subset:
            text = r["state"]["request"]
            true_choice = r["choice"]
            
            if kind == "sparse":
                q_vec = sparse_model.transform(text)
                sims = [cosine_sim(q_vec, v) for v in sparse_ref_vecs]
            elif kind == "neural":
                q_vec = neural_encode(text)
                sims = [cosine_sim(q_vec, v) for v in neural_train_vecs]
            else:  # hybrid
                q_s = sparse_model.transform(text)
                q_n = neural_encode(text)
                sims = [0.5 * cosine_sim(q_s, vs) + 0.5 * cosine_sim(q_n, vn) for vs, vn in zip(sparse_ref_vecs, neural_train_vecs)]
                
            sims = np.array(sims)
            top_idx = int(np.argmax(sims))
            pred_choice = train_choices[top_idx]
            if pred_choice == true_choice:
                correct_same_choice += 1
                
            # Margin between nearest same choice and nearest different choice
            same_sims = [s for s, c in zip(sims, train_choices) if c == true_choice]
            diff_sims = [s for s, c in zip(sims, train_choices) if c != true_choice]
            
            max_same = max(same_sims) if same_sims else 0.0
            max_diff = max(diff_sims) if diff_sims else 0.0
            margin = max_same - max_diff
            margins.append(margin)
            if max_diff > max_same:
                neg_separation_failures += 1
                
        # Contrastive sensitivity
        contrastive_hits = 0
        for text_a, choice_a, text_b, choice_b, note in CONTRASTIVE_PAIRS:
            if kind == "sparse":
                va, vb = sparse_model.transform(text_a), sparse_model.transform(text_b)
            elif kind == "neural":
                va, vb = neural_encode(text_a), neural_encode(text_b)
            else:
                va_s, vb_s = sparse_model.transform(text_a), sparse_model.transform(text_b)
                va_n, vb_n = neural_encode(text_a), neural_encode(text_b)
                sim_pair = 0.5 * cosine_sim(va_s, vb_s) + 0.5 * cosine_sim(va_n, vb_n)
                
            if kind != "hybrid":
                sim_pair = cosine_sim(va, vb)
            # If choices differ, pair similarity should NOT be excessively high (>0.85)
            if choice_a != choice_b:
                if sim_pair < 0.85:
                    contrastive_hits += 1
            else:
                if sim_pair > 0.60:
                    contrastive_hits += 1
                    
        return {
            "top1_same_choice_recall": round(correct_same_choice / len(test_subset), 4),
            "false_neighbor_rate": round(neg_separation_failures / len(test_subset), 4),
            "avg_margin": round(float(np.mean(margins)), 4),
            "median_margin": round(float(np.median(margins)), 4),
            "contrastive_robustness": round(contrastive_hits / len(CONTRASTIVE_PAIRS), 4),
        }

    results = {}
    print("\n1. Evaluating Sparse TF-IDF...")
    results["sparse_tfidf"] = {
        **eval_retrieval("sparse"),
        "latency_ms": round(sparse_latency_ms, 3),
        "vector_dims": len(sparse_model.vocab),
        "storage_kb_per_1k": round(len(sparse_model.vocab) * 4 * 1000 / 1024, 1),
        "model_memory_mb": 0.5,
    }
    
    print("2. Evaluating Neural ModernBERT...")
    results["neural_modernbert"] = {
        **eval_retrieval("neural"),
        "latency_ms": round(neural_latency_ms, 2),
        "vector_dims": 1024,
        "storage_kb_per_1k": round(1024 * 4 * 1000 / 1024, 1),
        "model_memory_mb": 918.0,
    }
    
    print("3. Evaluating Hybrid (Sparse + Neural)...")
    results["hybrid"] = {
        **eval_retrieval("hybrid"),
        "latency_ms": round(sparse_latency_ms + neural_latency_ms, 2),
        "vector_dims": len(sparse_model.vocab) + 1024,
        "storage_kb_per_1k": round((len(sparse_model.vocab) + 1024) * 4 * 1000 / 1024, 1),
        "model_memory_mb": 918.5,
    }
    
    out_file = benchmarks_dir / "results/representation_report.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(results, indent=2) + "\n")
    print("\nRepresentation Comparison Results:")
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    main()
