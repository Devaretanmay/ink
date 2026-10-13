"""Candidate Engine Evaluation and Selection Policy.

Compares multiple candidate engines on task-disjoint evidence and selects
the most efficient engine that satisfies the site's required quality.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

from .contracts import DecisionSite, Outcome, PromotionRequirements
from .representation import StateRepresentationLayer
from .verification import lower_bound


@dataclass(frozen=True)
class EngineEvaluationReport:
    name: str
    accuracy: float
    macro_f1: float
    verified_quality_mean: float
    quality_lower_bound: float
    p50_latency_ms: float
    memory_mb: float
    qualified: bool
    samples: int
    compile_time_ms: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_engine_candidate(
    engine_name: str,
    engine: Any,
    site: DecisionSite,
    train_rows: list[dict[str, Any]],
    val_rows: list[dict[str, Any]],
    requirements: PromotionRequirements,
    verifier: Any = None,
    rep_layer: StateRepresentationLayer | None = None,
) -> tuple[dict[str, Any], EngineEvaluationReport]:
    """Compile and evaluate a single candidate engine on validation rows."""
    compile_start = time.perf_counter()
    if engine_name in ("linear", "classifier") and rep_layer is not None:
        payload = engine.compile(site, train_rows, representation_layer=rep_layer)
    else:
        payload = engine.compile(site, train_rows)
    compile_time_ms = (time.perf_counter() - compile_start) * 1000.0

    correct = 0
    predictions = []
    latencies = []
    verified_outcomes = []
    choices = list(site.choices)

    # Class-wise metrics for macro F1
    true_counts = {c: 0 for c in choices}
    pred_counts = {c: 0 for c in choices}
    tp_counts = {c: 0 for c in choices}

    for row in val_rows:
        state = row["state"]
        true_choice = row["choice"]
        true_counts[true_choice] = true_counts.get(true_choice, 0) + 1

        t0 = time.perf_counter()
        try:
            pred_choice, _ = engine.predict(payload, state)
        except (KeyError, ValueError):
            # Engine cannot predict this state (e.g. ExactEngine on unseen state)
            pred_choice = choices[0]
        latencies.append((time.perf_counter() - t0) * 1000.0)

        is_correct = pred_choice == true_choice
        if is_correct:
            correct += 1
            tp_counts[pred_choice] = tp_counts.get(pred_choice, 0) + 1
        pred_counts[pred_choice] = pred_counts.get(pred_choice, 0) + 1
        predictions.append(pred_choice)

        if verifier is not None:
            cand_outcome = verifier(state, pred_choice)
            q = cand_outcome.quality if isinstance(cand_outcome, Outcome) else float(cand_outcome)
        elif is_correct and row.get("outcome"):
            q = float(row["outcome"]["quality"])
        else:
            q = 1.0 if is_correct else 0.0
        verified_outcomes.append(q)

    n = len(val_rows)
    accuracy = correct / n if n > 0 else 0.0

    # Macro F1 calculation
    f1_scores = []
    for c in choices:
        tp = tp_counts.get(c, 0)
        fp = pred_counts.get(c, 0) - tp
        fn = true_counts.get(c, 0) - tp
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0
        f1_scores.append(f1)
    macro_f1 = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0

    q_mean = sum(verified_outcomes) / n if n > 0 else 0.0
    q_lower = lower_bound(verified_outcomes)

    latencies.sort()
    p50_lat = latencies[len(latencies) // 2] if latencies else 0.01

    memory_mb = 0.05 if engine_name == "exact" else (0.1 if engine_name in ("linear", "classifier") else 800.0)

    # Qualified check against requirements
    qualified = bool(
        q_lower >= requirements.min_confidence
        and q_mean >= requirements.min_quality
        and n >= requirements.min_region_samples
    )

    report = EngineEvaluationReport(
        name=engine_name,
        accuracy=round(accuracy, 4),
        macro_f1=round(macro_f1, 4),
        verified_quality_mean=round(q_mean, 4),
        quality_lower_bound=round(q_lower, 4),
        p50_latency_ms=round(p50_lat, 4),
        memory_mb=memory_mb,
        qualified=qualified,
        samples=n,
        compile_time_ms=round(compile_time_ms, 2),
    )
    return payload, report


class EngineSelectionPolicy:
    """Selects the most efficient candidate engine that satisfies site requirements."""

    LATENCY_PRIORITY = {
        "exact": 1,
        "linear": 2,
        "classifier": 2,
        "decision": 3,
        "ink-decision-small": 3,
        "ink-decision-large": 4,
        "ink-decision-v1": 3,
    }

    @classmethod
    def select_best_engine(
        cls,
        evaluations: dict[str, tuple[dict[str, Any], EngineEvaluationReport]],
        requirements: PromotionRequirements,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Choose the optimal candidate engine and produce an audit trail."""
        reports = {name: rep for name, (_, rep) in evaluations.items()}
        eligible = [name for name, rep in reports.items() if rep.qualified]

        if eligible:
            # Sort eligible candidates by latency priority (lowest latency first)
            eligible.sort(
                key=lambda name: (
                    cls.LATENCY_PRIORITY.get(name, 99),
                    reports[name].p50_latency_ms,
                )
            )
            selected = eligible[0]
            selected_rep = reports[selected]
            why_selected = (
                f"{selected.upper()} selected: satisfies qualification "
                f"(quality_lower={selected_rep.quality_lower_bound} >= {requirements.min_confidence}) "
                f"with lowest execution overhead (p50={selected_rep.p50_latency_ms}ms, "
                f"{selected_rep.memory_mb}MB)."
            )
        else:
            # If none meet formal qualification, pick highest quality candidate
            selected = max(reports.keys(), key=lambda name: reports[name].quality_lower_bound)
            selected_rep = reports[selected]
            why_selected = (
                f"{selected.upper()} selected as best available candidate "
                f"(quality_lower={selected_rep.quality_lower_bound}), "
                f"pending further observation evidence."
            )

        payload = evaluations[selected][0]
        audit_record = {
            "selected_engine": selected,
            "why_selected": why_selected,
            "reports": {name: rep.to_dict() for name, rep in reports.items()},
        }
        return selected, payload, audit_record
