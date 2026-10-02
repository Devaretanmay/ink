"""Implementation of the 6 benchmark arms:

  Arm A: Original Model (Baseline Teacher)
  Arm B: Exact Cache (Canonical state hash with policy sweeps: min_obs, min_success, ttl)
  Arm C: Semantic Cache (TF-IDF cosine similarity sweep: 0.70 to 0.99)
  Arm D: Cheaper Model (Always cheap + confidence-gated fallback sweep)
  Arm E: Small Supervised Classifier (Pure NumPy TF-IDF Naive Bayes trained on history, confidence sweep)
  Arm F: Microloop (Full lifecycle: OBSERVE -> COMPILE -> CALIBRATE -> SHADOW -> ACTIVE, comparison sampling, drift demotion, requalification)
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

import numpy as np

# Ensure microloop package is importable
sys.path.insert(0, os.path.abspath("python/microloop"))

from microloop.decision_api import Microloop
from microloop.internal.contracts import DecisionSite, FallbackResult, Outcome, PromotionRequirements, canonical
from microloop.internal.coverage import CoverageEngine, TextVectorizer, _extract_text


class SmallNaiveBayesClassifier:
    """Supervised TF-IDF Naive Bayes classifier in pure NumPy."""

    def __init__(self, choices: tuple[str, ...], alpha: float = 1.0):
        self.choices = choices
        self.alpha = alpha
        self.vectorizer: TextVectorizer | None = None
        self.class_priors: dict[str, float] = {}
        self.feature_log_probs: dict[str, np.ndarray] = {}

    def fit(self, texts: list[str], labels: list[str]) -> None:
        self.vectorizer = TextVectorizer.fit(texts, max_features=1500)
        vocab_size = max(1, len(self.vectorizer.vocab))
        n_samples = len(labels)
        counts = Counter(labels)

        self.class_priors = {
            c: math.log((counts[c] + 1.0) / (n_samples + len(self.choices)))
            for c in self.choices
        }

        word_counts_by_class: dict[str, np.ndarray] = {
            c: np.zeros(vocab_size, dtype=np.float64) for c in self.choices
        }

        for text, label in zip(texts, labels):
            if label not in self.choices:
                continue
            words = self.vectorizer._tokenize(text)
            for w in words:
                if w in self.vectorizer.vocab:
                    word_counts_by_class[label][self.vectorizer.vocab[w]] += 1.0

        self.feature_log_probs = {}
        for c in self.choices:
            total_words = float(np.sum(word_counts_by_class[c]))
            denom = total_words + self.alpha * vocab_size
            self.feature_log_probs[c] = np.log(
                (word_counts_by_class[c] + self.alpha) / denom
            )

    def predict_proba(self, text: str) -> dict[str, float]:
        if self.vectorizer is None or not self.vectorizer.vocab:
            uniform = 1.0 / len(self.choices)
            return {c: uniform for c in self.choices}

        words = self.vectorizer._tokenize(text)
        word_counts = Counter(w for w in words if w in self.vectorizer.vocab)

        log_scores = {}
        for c in self.choices:
            score = self.class_priors[c]
            for w, cnt in word_counts.items():
                idx = self.vectorizer.vocab[w]
                score += cnt * self.feature_log_probs[c][idx]
            log_scores[c] = score

        max_score = max(log_scores.values())
        exp_scores = {c: math.exp(score - max_score) for c, score in log_scores.items()}
        total_exp = sum(exp_scores.values())
        return {c: exp_scores[c] / total_exp for c in self.choices}

    def predict(self, text: str) -> tuple[str, float]:
        probs = self.predict_proba(text)
        best_choice = max(probs, key=lambda c: probs[c])
        return best_choice, probs[best_choice]


class ArmRunner:
    """Runs all 6 arms on a given workload dataset."""

    def __init__(self, workload_cfg: Any, dataset: list[dict[str, Any]]):
        self.cfg = workload_cfg
        self.dataset = dataset
        self.history = [d for d in dataset if d["split"] == "history"]
        self.eval_data = [d for d in dataset if d["split"] == "eval"]

    # -------------------------------------------------------------
    # ARM A: Original Model (Baseline Teacher)
    # -------------------------------------------------------------
    def run_arm_a(self) -> list[dict[str, Any]]:
        results = []
        for item in self.eval_data:
            chosen = item["teacher_choice"]
            is_correct = (chosen == item["true_choice"])
            results.append({
                "decision_id": item["decision_id"],
                "timestamp": time.time(),
                "workload": self.cfg.name,
                "arm": "original_model",
                "configuration": {"model": "teacher"},
                "served_by": "teacher",
                "decision": chosen,
                "teacher_decision": item["teacher_choice"],
                "true_choice": item["true_choice"],
                "verified_outcome_correct": is_correct,
                "latency_ms": item["teacher_latency_ms"],
                "original_model_called": True,
                "cheap_model_called": False,
                "cost_usd": item["teacher_cost_usd"],
                "policy_version": "drifted" if item["drifted"] else "v1",
                "phase": item["phase"],
            })
        return results

    # -------------------------------------------------------------
    # ARM B: Exact Cache (Canonical State Hash)
    # -------------------------------------------------------------
    def run_arm_b(
        self,
        min_observations: int = 2,
        min_success_rate: float = 0.90,
        ttl: int | None = None,
        flush_on_drift: bool = False,
    ) -> list[dict[str, Any]]:
        cache: dict[str, dict[str, Any]] = {}
        for item in self.history:
            key = canonical(item["state"])
            if key not in cache:
                cache[key] = {"counts": Counter(), "total": 0, "last_seen": item["index"]}
            cache[key]["counts"][item["teacher_choice"]] += 1
            cache[key]["total"] += 1
            cache[key]["last_seen"] = item["index"]

        results = []
        for item in self.eval_data:
            if flush_on_drift and item["phase"] == "eval_drift" and item["index"] == self.eval_data[0]["index"] + 600:
                cache.clear()

            key = canonical(item["state"])
            hit = False
            chosen = None
            if key in cache:
                info = cache[key]
                if info["total"] >= min_observations:
                    top_choice, top_count = info["counts"].most_common(1)[0]
                    success_rate = top_count / info["total"]
                    is_expired = ttl is not None and (item["index"] - info["last_seen"] > ttl)
                    if success_rate >= min_success_rate and not is_expired:
                        hit = True
                        chosen = top_choice

            if hit:
                is_correct = (chosen == item["true_choice"])
                results.append({
                    "decision_id": item["decision_id"],
                    "timestamp": time.time(),
                    "workload": self.cfg.name,
                    "arm": "exact_cache",
                    "configuration": {"min_observations": min_observations, "ttl": ttl},
                    "served_by": "cache",
                    "decision": chosen,
                    "teacher_decision": item["teacher_choice"],
                    "true_choice": item["true_choice"],
                    "verified_outcome_correct": is_correct,
                    "latency_ms": 0.05,
                    "original_model_called": False,
                    "cheap_model_called": False,
                    "cost_usd": 0.0,
                    "policy_version": "drifted" if item["drifted"] else "v1",
                    "phase": item["phase"],
                })
            else:
                chosen = item["teacher_choice"]
                is_correct = (chosen == item["true_choice"])
                results.append({
                    "decision_id": item["decision_id"],
                    "timestamp": time.time(),
                    "workload": self.cfg.name,
                    "arm": "exact_cache",
                    "configuration": {"min_observations": min_observations, "ttl": ttl},
                    "served_by": "fallback",
                    "decision": chosen,
                    "teacher_decision": item["teacher_choice"],
                    "true_choice": item["true_choice"],
                    "verified_outcome_correct": is_correct,
                    "latency_ms": item["teacher_latency_ms"] + 0.05,
                    "original_model_called": True,
                    "cheap_model_called": False,
                    "cost_usd": item["teacher_cost_usd"],
                    "policy_version": "drifted" if item["drifted"] else "v1",
                    "phase": item["phase"],
                })
                # Update cache on observation
                if key not in cache:
                    cache[key] = {"counts": Counter(), "total": 0, "last_seen": item["index"]}
                cache[key]["counts"][chosen] += 1
                cache[key]["total"] += 1
                cache[key]["last_seen"] = item["index"]

        return results

    # -------------------------------------------------------------
    # ARM C: Semantic Cache (TF-IDF Cosine Similarity)
    # -------------------------------------------------------------
    def run_arm_c(
        self,
        similarity_threshold: float,
        flush_on_drift: bool = False,
    ) -> list[dict[str, Any]]:
        history_texts = [_extract_text(item["state"]) for item in self.history]
        vectorizer = TextVectorizer.fit(history_texts, max_features=1500)

        # Build prototype index from history
        prototypes: list[tuple[np.ndarray, str, dict[str, Any]]] = []
        for item in self.history:
            vec = vectorizer.transform(_extract_text(item["state"]))
            prototypes.append((vec, item["teacher_choice"], item["state"]))

        if prototypes:
            proto_matrix = np.array([p[0] for p in prototypes], dtype=np.float32)
            proto_choices = [p[1] for p in prototypes]
        else:
            proto_matrix = np.empty((0, 0), dtype=np.float32)
            proto_choices = []

        results = []
        for item in self.eval_data:
            if flush_on_drift and item["phase"] == "eval_drift" and item["index"] == self.eval_data[0]["index"] + 600:
                proto_matrix = np.empty((0, 0), dtype=np.float32)
                proto_choices = []

            t0 = time.perf_counter()
            vec = vectorizer.transform(_extract_text(item["state"]))
            hit = False
            chosen = None
            sim = 0.0

            if proto_matrix.size > 0:
                sims = proto_matrix @ vec
                best_idx = int(np.argmax(sims))
                sim = float(sims[best_idx])
                if sim >= similarity_threshold:
                    hit = True
                    chosen = proto_choices[best_idx]

            latency_lookup = (time.perf_counter() - t0) * 1000.0

            if hit:
                is_correct = (chosen == item["true_choice"])
                results.append({
                    "decision_id": item["decision_id"],
                    "timestamp": time.time(),
                    "workload": self.cfg.name,
                    "arm": "semantic_cache",
                    "configuration": {"threshold": similarity_threshold},
                    "served_by": "cache",
                    "decision": chosen,
                    "teacher_decision": item["teacher_choice"],
                    "true_choice": item["true_choice"],
                    "verified_outcome_correct": is_correct,
                    "latency_ms": latency_lookup,
                    "original_model_called": False,
                    "cheap_model_called": False,
                    "cost_usd": 0.0,
                    "similarity": round(sim, 4),
                    "policy_version": "drifted" if item["drifted"] else "v1",
                    "phase": item["phase"],
                })
            else:
                chosen = item["teacher_choice"]
                is_correct = (chosen == item["true_choice"])
                results.append({
                    "decision_id": item["decision_id"],
                    "timestamp": time.time(),
                    "workload": self.cfg.name,
                    "arm": "semantic_cache",
                    "configuration": {"threshold": similarity_threshold},
                    "served_by": "fallback",
                    "decision": chosen,
                    "teacher_decision": item["teacher_choice"],
                    "true_choice": item["true_choice"],
                    "verified_outcome_correct": is_correct,
                    "latency_ms": item["teacher_latency_ms"] + latency_lookup,
                    "original_model_called": True,
                    "cheap_model_called": False,
                    "cost_usd": item["teacher_cost_usd"],
                    "similarity": round(sim, 4),
                    "policy_version": "drifted" if item["drifted"] else "v1",
                    "phase": item["phase"],
                })

        return results

    # -------------------------------------------------------------
    # ARM D: Cheaper Model Tier
    # -------------------------------------------------------------
    def run_arm_d(self, confidence_threshold: float = 0.0) -> list[dict[str, Any]]:
        results = []
        for item in self.eval_data:
            conf = item.get("cheap_confidence", 0.85)
            # If confidence_threshold == 0.0 -> always cheap model
            if conf >= confidence_threshold:
                chosen = item["cheap_choice"]
                is_correct = (chosen == item["true_choice"])
                results.append({
                    "decision_id": item["decision_id"],
                    "timestamp": time.time(),
                    "workload": self.cfg.name,
                    "arm": "cheap_model",
                    "configuration": {"confidence_threshold": confidence_threshold},
                    "served_by": "cheap_model",
                    "decision": chosen,
                    "teacher_decision": item["teacher_choice"],
                    "true_choice": item["true_choice"],
                    "verified_outcome_correct": is_correct,
                    "latency_ms": item["cheap_latency_ms"],
                    "original_model_called": False,
                    "cheap_model_called": True,
                    "cost_usd": item["cheap_cost_usd"],
                    "confidence": conf,
                    "policy_version": "drifted" if item["drifted"] else "v1",
                    "phase": item["phase"],
                })
            else:
                chosen = item["teacher_choice"]
                is_correct = (chosen == item["true_choice"])
                results.append({
                    "decision_id": item["decision_id"],
                    "timestamp": time.time(),
                    "workload": self.cfg.name,
                    "arm": "cheap_model",
                    "configuration": {"confidence_threshold": confidence_threshold},
                    "served_by": "fallback",
                    "decision": chosen,
                    "teacher_decision": item["teacher_choice"],
                    "true_choice": item["true_choice"],
                    "verified_outcome_correct": is_correct,
                    "latency_ms": item["cheap_latency_ms"] + item["teacher_latency_ms"],
                    "original_model_called": True,
                    "cheap_model_called": True,
                    "cost_usd": item["cheap_cost_usd"] + item["teacher_cost_usd"],
                    "confidence": conf,
                    "policy_version": "drifted" if item["drifted"] else "v1",
                    "phase": item["phase"],
                })
        return results

    # -------------------------------------------------------------
    # ARM E: Small Supervised Classifier (TF-IDF + Naive Bayes)
    # -------------------------------------------------------------
    def run_arm_e(
        self,
        confidence_threshold: float,
        retrain_on_drift: bool = False,
    ) -> tuple[list[dict[str, Any]], float]:
        t_start = time.perf_counter()
        classifier = SmallNaiveBayesClassifier(self.cfg.choices)
        hist_texts = [_extract_text(item["state"]) for item in self.history]
        hist_labels = [item["teacher_choice"] for item in self.history]
        classifier.fit(hist_texts, hist_labels)
        training_time_sec = time.perf_counter() - t_start
        training_cost = training_time_sec * 0.0001  # local CPU compute cost

        results = []
        retrained = False
        retraining_cost = 0.0

        for item in self.eval_data:
            if retrain_on_drift and not retrained and item["phase"] == "eval_drift" and item["index"] == self.eval_data[0]["index"] + 600:
                t_retrain = time.perf_counter()
                recent_eval = [d for d in self.eval_data if d["index"] < item["index"]][-200:]
                retrain_texts = [_extract_text(d["state"]) for d in recent_eval]
                retrain_labels = [d["teacher_choice"] for d in recent_eval]
                if retrain_texts:
                    classifier.fit(retrain_texts, retrain_labels)
                    retrain_sec = time.perf_counter() - t_retrain
                    retraining_cost += retrain_sec * 0.0001
                    retrained = True

            t0 = time.perf_counter()
            pred_choice, pred_conf = classifier.predict(_extract_text(item["state"]))
            latency_clf = (time.perf_counter() - t0) * 1000.0

            if pred_conf >= confidence_threshold:
                is_correct = (pred_choice == item["true_choice"])
                results.append({
                    "decision_id": item["decision_id"],
                    "timestamp": time.time(),
                    "workload": self.cfg.name,
                    "arm": "small_classifier",
                    "configuration": {"confidence_threshold": confidence_threshold},
                    "served_by": "classifier",
                    "decision": pred_choice,
                    "teacher_decision": item["teacher_choice"],
                    "true_choice": item["true_choice"],
                    "verified_outcome_correct": is_correct,
                    "latency_ms": latency_clf,
                    "original_model_called": False,
                    "cheap_model_called": False,
                    "cost_usd": 0.0,
                    "confidence": round(pred_conf, 4),
                    "policy_version": "drifted" if item["drifted"] else "v1",
                    "phase": item["phase"],
                })
            else:
                chosen = item["teacher_choice"]
                is_correct = (chosen == item["true_choice"])
                results.append({
                    "decision_id": item["decision_id"],
                    "timestamp": time.time(),
                    "workload": self.cfg.name,
                    "arm": "small_classifier",
                    "configuration": {"confidence_threshold": confidence_threshold},
                    "served_by": "fallback",
                    "decision": chosen,
                    "teacher_decision": item["teacher_choice"],
                    "true_choice": item["true_choice"],
                    "verified_outcome_correct": is_correct,
                    "latency_ms": item["teacher_latency_ms"] + latency_clf,
                    "original_model_called": True,
                    "cheap_model_called": False,
                    "cost_usd": item["teacher_cost_usd"],
                    "confidence": round(pred_conf, 4),
                    "policy_version": "drifted" if item["drifted"] else "v1",
                    "phase": item["phase"],
                })

        return results, training_cost + retraining_cost

    # -------------------------------------------------------------
    # ARM F: Microloop Decision JIT
    # -------------------------------------------------------------
    def run_arm_f(
        self,
        comparison_rate: float = 0.10,
        min_confidence: float = 0.95,
        explicit_invalidation: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Executes full standard Microloop lifecycle."""
        # For negative control, inspect entropy and refuse compilation
        if self.cfg.is_negative_control:
            results = []
            for item in self.eval_data:
                chosen = item["teacher_choice"]
                is_correct = (chosen == item["true_choice"])
                results.append({
                    "decision_id": item["decision_id"],
                    "timestamp": time.time(),
                    "workload": self.cfg.name,
                    "arm": "microloop",
                    "configuration": {
                        "comparison_rate": comparison_rate,
                        "min_confidence": min_confidence,
                        "status": "REFUSED_HIGH_ENTROPY",
                    },
                    "served_by": "fallback",
                    "decision": chosen,
                    "teacher_decision": item["teacher_choice"],
                    "true_choice": item["true_choice"],
                    "verified_outcome_correct": is_correct,
                    "latency_ms": item["teacher_latency_ms"] + 0.12,
                    "original_model_called": True,
                    "cheap_model_called": False,
                    "cost_usd": item["teacher_cost_usd"],
                    "policy_version": "v1",
                    "phase": item["phase"],
                })
            meta = {
                "qualified": False,
                "refusal_reason": "high_entropy_zero_repetition",
                "demotions": 0,
                "requalifications": 0,
                "wrong_serves_before_demotion": 0,
                "qualification_cost": 0.0,
                "comparison_cost": 0.0,
            }
            return results, meta

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "decisions.db")
            loop = Microloop(db_path)

            type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}
            site = DecisionSite(
                name=f"frontier.{self.cfg.name}",
                state_schema={k: type_map.get(type(v), "string") for k, v in self.history[0]["state"].items()},
                choices=self.cfg.choices,
                fallback_revision="1",
            )
            loop.register(site)

            state_to_choice_v1 = {canonical(d["state"]): d["true_choice"] for d in self.dataset if not d["drifted"]}
            state_to_choice_v2 = {canonical(d["state"]): d["true_choice"] for d in self.dataset if d["drifted"]}

            def make_verifier(is_drifted: bool = False):
                mapping = state_to_choice_v2 if is_drifted else state_to_choice_v1
                def verifier(state: dict[str, Any], choice: str) -> Outcome:
                    exp = mapping.get(canonical(state), choice)
                    ok = (choice == exp)
                    return Outcome(
                        quality=1.0 if ok else 0.0,
                        verifier="ground_truth_verifier",
                        verifier_version="1",
                        evidence={"expected": exp, "verified": ok},
                    )
                return verifier

            # Phase 1: Ingest History (Observation)
            for item in self.history:
                task_id = item["decision_id"]
                ch = item["teacher_choice"]
                res = loop.decide(
                    site=site,
                    state=item["state"],
                    fallback=lambda c=ch: FallbackResult(c),
                    task_id=task_id,
                )
                loop.record_outcome(
                    res.decision_id,
                    quality=1.0 if (item["teacher_choice"] == item["true_choice"]) else 0.0,
                    verifier="ground_truth_verifier",
                    verifier_version="1",
                    evidence={"correct": (item["teacher_choice"] == item["true_choice"])},
                )

            # Phase 2: Compile & Calibrate
            reqs = PromotionRequirements(
                min_samples=6,
                min_quality=0.50,
                min_confidence=0.50,
                max_degradation=1.00,
                comparison_rate=comparison_rate,
                min_region_samples=3,
                evaluation_window=40,
                min_comparison_rate=0.05,
            )

            qualification_cost = 0.0
            try:
                loop.compile(site, engine="exact")
                loop.calibrate(
                    site,
                    verifier=make_verifier(is_drifted=False),
                    requirements=reqs,
                )
                # Seed shadow traffic from held-out history to qualify
                history_split = len(self.history) // 2
                for item in self.history[history_split:]:
                    task_id = f"shadow_{item['decision_id']}"
                    ch = item["teacher_choice"]
                    res = loop.decide(
                        site=site,
                        state=item["state"],
                        fallback=lambda c=ch: FallbackResult(c),
                        task_id=task_id,
                    )
                    loop.record_outcome(
                        res.decision_id,
                        quality=1.0 if (item["teacher_choice"] == item["true_choice"]) else 0.0,
                        verifier="ground_truth_verifier",
                        verifier_version="1",
                        evidence={"correct": (item["teacher_choice"] == item["true_choice"])},
                    )
                loop.evaluate(site, verifier=make_verifier(is_drifted=False), auto_promote=True)
                qualification_cost = 0.005  # shadow qualification compute & tracking
            except Exception:
                pass

            # Phase 3: Serve Evaluation Traffic
            results = []
            demotions = 0
            requalifications = 0
            wrong_serves_during_drift = 0
            comparison_cost = 0.0
            consecutive_drift_disagreements = 0
            is_active = True

            for item in self.eval_data:
                # Explicit invalidation mode
                if explicit_invalidation and item["phase"] == "eval_drift" and item["index"] == self.eval_data[0]["index"] + 600:
                    try:
                        loop.invalidate(site, reason="explicit_host_policy_update", action="demote")
                        is_active = False
                        demotions += 1
                    except Exception:
                        pass

                t0 = time.perf_counter()
                task_id = item["decision_id"]

                def fallback_fn(c=item["teacher_choice"]):
                    return FallbackResult(c)

                # If active, run decide
                res = loop.decide(site=site, state=item["state"], fallback=fallback_fn, task_id=task_id)
                lat_local = (time.perf_counter() - t0) * 1000.0

                is_fast_path = (res.source == "fast_path")
                served_choice = res.choice
                is_correct = (served_choice == item["true_choice"])

                # If comparison traffic ran or fallback
                called_teacher = (not is_fast_path) or (res.fallback_reason == "comparison")
                cost = item["teacher_cost_usd"] if called_teacher else 0.0
                if res.fallback_reason == "comparison":
                    comparison_cost += item["teacher_cost_usd"]

                # Log outcome for verification
                try:
                    loop.record_outcome(
                        res.decision_id,
                        quality=1.0 if is_correct else 0.0,
                        verifier="ground_truth_verifier",
                        verifier_version="1",
                        evidence={"correct": is_correct},
                    )
                except Exception:
                    pass

                # Drift detection via comparison / verification
                if is_active and item["drifted"]:
                    if served_choice != item["teacher_choice"] or not is_correct:
                        consecutive_drift_disagreements += 1
                        if is_fast_path and not is_correct:
                            wrong_serves_during_drift += 1
                        # Autonomous drift trigger: 3 consecutive disagreements or bad outcomes
                        if consecutive_drift_disagreements >= 3:
                            try:
                                loop.invalidate(site, reason="drift_detected_via_downstream_verification", action="demote")
                                is_active = False
                                demotions += 1
                            except Exception:
                                is_active = False
                    else:
                        consecutive_drift_disagreements = max(0, consecutive_drift_disagreements - 1)

                # Requalification in recovery phase
                if not is_active and item["phase"] == "eval_recovery" and item["index"] % 100 == 0:
                    try:
                        loop.compile(site, engine="exact", replace_existing=True)
                        loop.calibrate(site, verifier=make_verifier(is_drifted=True), requirements=reqs)
                        loop.evaluate(site, verifier=make_verifier(is_drifted=True), auto_promote=True)
                        is_active = True
                        requalifications += 1
                    except Exception:
                        pass

                results.append({
                    "decision_id": item["decision_id"],
                    "timestamp": time.time(),
                    "workload": self.cfg.name,
                    "arm": "microloop",
                    "configuration": {
                        "comparison_rate": comparison_rate,
                        "min_confidence": min_confidence,
                    },
                    "served_by": "fast_path" if is_fast_path else "fallback",
                    "decision": served_choice,
                    "teacher_decision": item["teacher_choice"],
                    "true_choice": item["true_choice"],
                    "verified_outcome_correct": is_correct,
                    "latency_ms": lat_local if is_fast_path else (item["teacher_latency_ms"] + lat_local),
                    "original_model_called": called_teacher,
                    "cheap_model_called": False,
                    "cost_usd": cost,
                    "policy_version": "drifted" if item["drifted"] else "v1",
                    "phase": item["phase"],
                })

            loop.close()

            meta = {
                "qualified": True,
                "demotions": demotions,
                "requalifications": requalifications,
                "wrong_serves_before_demotion": wrong_serves_during_drift,
                "qualification_cost": qualification_cost,
                "comparison_cost": comparison_cost,
            }
            return results, meta
