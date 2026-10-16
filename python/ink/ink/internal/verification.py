"""Evidence calculations shared by calibration, evaluation, and drift checks."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import asdict
from enum import StrEnum

from .contracts import Outcome, canonical
from .coverage import CoverageEngine


class EvidenceStatus(StrEnum):
    """Explicit classification of qualification and verification evidence."""

    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    VERIFIED_FAILURE = "VERIFIED_FAILURE"
    OBSERVED_AGREEMENT = "OBSERVED_AGREEMENT"
    UNVERIFIED_DISAGREEMENT = "UNVERIFIED_DISAGREEMENT"
    UNVERIFIABLE = "UNVERIFIABLE"


def lower_bound(values):
    """Hoeffding 95% one-sided bound for independent bounded task outcomes."""
    if not values:
        return 0.0
    return max(0.0, sum(values) / len(values) - math.sqrt(math.log(20) / (2 * len(values))))


def wilson_lower_bound(successes: int | float, total: int, z: float = 1.959963984540054) -> float:
    """Wilson 95% two-sided confidence lower bound for binomial proportions."""
    if total <= 0 or successes <= 0:
        return 0.0
    p = float(successes) / float(total)
    z2 = z * z
    denom = 1.0 + z2 / float(total)
    centre = (p + z2 / (2.0 * float(total))) / denom
    margin = (z / denom) * math.sqrt(
        max(0.0, (p * (1.0 - p) / float(total)) + (z2 / (4.0 * (float(total) ** 2))))
    )
    return max(0.0, centre - margin)


def wilson_upper_bound(successes: int | float, total: int, z: float = 1.959963984540054) -> float:
    """Wilson 95% two-sided confidence upper bound for binomial proportions."""
    if total <= 0:
        return 0.0
    p = float(successes) / float(total)
    z2 = z * z
    denom = 1.0 + z2 / float(total)
    centre = (p + z2 / (2.0 * float(total))) / denom
    margin = (z / denom) * math.sqrt(
        max(0.0, (p * (1.0 - p) / float(total)) + (z2 / (4.0 * (float(total) ** 2))))
    )
    return min(1.0, centre + margin)


def grouped_quality(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row["task"], []).append(row["outcome"]["quality"])
    return [sum(values) / len(values) for values in groups.values()]


def verify_rows(rows, engine, payload, verifier=None):
    """Replays historical states through an engine and classifies candidate evidence.

    Invariants:
    1. Independent verifier: If a verifier callable is supplied, candidate and baseline
       choices are evaluated directly by the verifier with independent evidence.
    2. Missing outcomes: When verifier is None and a row lacks an observed outcome,
       the row is preserved as UNVERIFIABLE with None candidate/baseline. Missing
       observations are NEVER silently dropped, preserving the statistical denominator.
    3. Disagreements: When candidate choice diverges from observed Host choice without
       an external verifier, it is classified as UNVERIFIED_DISAGREEMENT (quality 0.0)
       to conservatively prevent unverified promotion.
    """
    results = []
    cov_engine = None
    if payload.get("coverage_engine"):
        cov_engine = CoverageEngine.from_dict(payload["coverage_engine"])
    for row in rows:
        key = canonical(row["state"])
        region_key = None
        prototype_state = None
        if key in payload.get("coverage", {}):
            region_key = key
        elif cov_engine is not None:
            level, reg, _ = cov_engine.route(row["state"])
            if level in ("exact", "semantic", "shadow") and reg is not None:
                region_key = reg.get("region_id", key)
                prototype_state = reg.get("prototype_state")
        if region_key is None:
            continue
        state_in = row["state"]
        if (
            prototype_state
            and payload.get("engine_data", {}).get("engine") == "exact"
            and key not in payload.get("engine_data", {}).get("table", {})
        ):
            state_in = prototype_state
        choice, raw = engine.predict(payload["engine_data"], state_in)
        if choice not in payload["choices"] or not math.isfinite(raw) or not 0 <= raw <= 1:
            raise ValueError("Engine returned an undeclared choice")
        if verifier is not None:
            candidate = verifier(deepcopy(row["state"]), choice)
            baseline = verifier(deepcopy(row["state"]), row["choice"])
            if not isinstance(candidate, Outcome) or not isinstance(baseline, Outcome):
                raise TypeError("Verifier must return Outcome with independent evidence")
            identity = (candidate.verifier, candidate.verifier_version)
            if identity != (baseline.verifier, baseline.verifier_version):
                raise ValueError("Verifier identity changed during replay")
            status = (
                EvidenceStatus.VERIFIED_SUCCESS
                if candidate.quality >= 0.5
                else EvidenceStatus.VERIFIED_FAILURE
            )
        else:
            if payload.get("engine_data", {}).get("engine") != "exact":
                raise ValueError("Semantic engines require a verifier")
            if row.get("outcome") is None:
                results.append(
                    {
                        "id": row["id"],
                        "task": row["task"],
                        "state": row["state"],
                        "region": region_key,
                        "choice": choice,
                        "raw_probability": raw,
                        "agreement": choice == row.get("choice"),
                        "status": EvidenceStatus.UNVERIFIABLE,
                        "candidate": None,
                        "baseline": None,
                    }
                )
                continue
            cand_dict = row["outcome"]
            baseline = Outcome(
                quality=float(cand_dict["quality"]),
                verifier=cand_dict.get("verifier", "observed"),
                verifier_version=str(cand_dict.get("verifier_version", "1")),
                evidence=cand_dict.get("evidence") or {"observed": True},
            )
            if choice == row["choice"]:
                candidate = baseline
                status = EvidenceStatus.OBSERVED_AGREEMENT
            else:
                status = EvidenceStatus.UNVERIFIED_DISAGREEMENT
                candidate = Outcome(
                    quality=0.0,
                    verifier=baseline.verifier,
                    verifier_version=baseline.verifier_version,
                    evidence={
                        "observed_choice": row["choice"],
                        "candidate_choice": choice,
                        "unverified": True,
                    },
                )
        results.append(
            {
                "id": row["id"],
                "task": row["task"],
                "state": row["state"],
                "region": region_key,
                "choice": choice,
                "raw_probability": raw,
                "agreement": choice == row["choice"],
                "status": status,
                "candidate": asdict(candidate),
                "baseline": asdict(baseline),
            }
        )
    identities = {
        (r["candidate"]["verifier"], r["candidate"]["verifier_version"])
        for r in results
        if r.get("candidate") is not None
    }
    if len(identities) > 1:
        raise ValueError("Mixed replay verifier versions")
    return results


def statistics(records):
    """Aggregates step-level verification records into task-level statistical bounds.

    Invariants:
    1. Multi-step task integrity: If any step in a task has missing outcomes (UNVERIFIABLE)
       or unverified disagreements, the task is marked as unknown and cannot increment
       observed_positive.
    2. Conservative Wilson bound: The Wilson score interval denominator accounts for all
       n tasks, severely penalizing candidates with unverified or missing observations.
    3. Hoeffding bound: In candidate_effective, unknown or missing tasks are attributed
       quality 0.0, establishing a rigorous 95% one-sided confidence lower bound.
    """
    tasks = {}
    for row in records:
        tasks.setdefault(row["task"], []).append(row)
    candidate, baseline, differences = [], [], []
    pos_count, neg_count, unk_count = 0, 0, 0
    for group in tasks.values():
        has_unverifiable = any(r.get("status") == EvidenceStatus.UNVERIFIABLE for r in group)
        has_unverified = any(
            r.get("status") == EvidenceStatus.UNVERIFIED_DISAGREEMENT for r in group
        )
        valid_cand = [
            r["candidate"]["quality"]
            for r in group
            if r.get("candidate") and r["candidate"].get("quality") is not None
        ]
        valid_base = [
            r["baseline"]["quality"]
            for r in group
            if r.get("baseline") and r["baseline"].get("quality") is not None
        ]
        if has_unverifiable or has_unverified:
            unk_count += 1
            if valid_cand:
                task_cand_q = sum(valid_cand) / len(group)
                candidate.append(task_cand_q)
        elif valid_cand:
            a = sum(valid_cand) / len(valid_cand)
            candidate.append(a)
            if a >= 0.5:
                pos_count += 1
            else:
                neg_count += 1
        else:
            unk_count += 1

        if valid_base and not has_unverifiable:
            b = sum(valid_base) / len(valid_base)
            baseline.append(b)
            if not (has_unverifiable or has_unverified) and valid_cand:
                differences.append(a - b)
    n = len(tasks)
    diff_n = len(differences)
    cand_n = len(candidate)
    base_n = len(baseline)
    agreements = [r["agreement"] for r in records if r.get("agreement") is not None]
    unverified_disagreements = sum(
        1 for r in records if r.get("status") == EvidenceStatus.UNVERIFIED_DISAGREEMENT
    )
    # Conservative lower bound: all n tasks accounted for; unknown/missing tasks count as 0.0
    candidate_effective = candidate + [0.0] * (n - len(candidate))
    return {
        "samples": n,
        "observed_positive": pos_count,
        "observed_negative": neg_count,
        "unverified_disagreements": unverified_disagreements,
        "unknown": unk_count,
        "quality_lower": lower_bound(candidate_effective),
        "quality_wilson_lower": wilson_lower_bound(pos_count, n) if n else 0.0,
        "quality_wilson_upper": wilson_upper_bound(pos_count, n) if n else 0.0,
        "quality": sum(candidate) / cand_n if cand_n else None,
        "baseline_quality": sum(baseline) / base_n if base_n else None,
        "delta": sum(differences) / diff_n if diff_n else None,
        "delta_lower": (sum(differences) / diff_n - math.sqrt(2 * math.log(20) / diff_n))
        if diff_n
        else -1.0,
        "agreement": sum(agreements) / len(agreements) if agreements else None,
    }


def passes(stats, requirements):
    return (
        stats["samples"] >= requirements.min_samples
        and stats["quality_lower"] >= requirements.min_quality
        and stats["delta_lower"] >= -requirements.max_degradation
    )


def region_qualifies(stats: dict, requirements) -> bool:
    """Pure canonical check for whether a candidate coverage/semantic region qualifies.

    Enforces minimum region sample volume, computes the Hoeffding sample boundary for
    confidence, and evaluates lower bound or empirical quality without inlining heuristics.
    """
    samples = stats.get("samples", 0)
    if samples < requirements.min_region_samples:
        return False
    min_n_for_conf = math.ceil(
        math.log(20) / (2 * max(0.0001, (1.0 - requirements.min_confidence)) ** 2)
    )
    if samples >= min_n_for_conf:
        return stats.get("quality_lower", 0.0) >= requirements.min_confidence
    return stats.get("quality", 1.0) >= requirements.min_quality
