"""Evidence calculations shared by calibration, evaluation, and drift checks."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import asdict

from .contracts import Outcome, canonical
from .coverage import CoverageEngine


def lower_bound(values):
    """Hoeffding 95% one-sided bound for independent bounded task outcomes."""
    if not values:
        return 0.0
    return max(0.0, sum(values) / len(values) - math.sqrt(math.log(20) / (2 * len(values))))


def grouped_quality(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row["task"], []).append(row["outcome"]["quality"])
    return [sum(values) / len(values) for values in groups.values()]


def verify_rows(rows, engine, payload, verifier):
    results = []
    cov_engine = None
    if payload.get("coverage_engine"):
        cov_engine = CoverageEngine.from_dict(payload["coverage_engine"])
    for row in rows:
        if row["outcome"] is None:
            continue
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
        # Verify both actions in the same replay environment; never transfer a factual
        # fallback outcome to an unexecuted candidate, including when they agree.
        candidate = verifier(deepcopy(row["state"]), choice)
        baseline = verifier(deepcopy(row["state"]), row["choice"])
        if not isinstance(candidate, Outcome) or not isinstance(baseline, Outcome):
            raise TypeError("Verifier must return Outcome with independent evidence")
        identity = (candidate.verifier, candidate.verifier_version)
        if identity != (baseline.verifier, baseline.verifier_version):
            raise ValueError("Verifier identity changed during replay")
        results.append(
            {
                "id": row["id"],
                "task": row["task"],
                "state": row["state"],
                "region": region_key,
                "choice": choice,
                "raw_probability": raw,
                "agreement": choice == row["choice"],
                "candidate": asdict(candidate),
                "baseline": asdict(baseline),
            }
        )
    identities = {(r["candidate"]["verifier"], r["candidate"]["verifier_version"]) for r in results}
    if len(identities) > 1:
        raise ValueError("Mixed replay verifier versions")
    return results


def statistics(records):
    tasks = {}
    for row in records:
        tasks.setdefault(row["task"], []).append(row)
    candidate, baseline, differences = [], [], []
    for group in tasks.values():
        a = sum(r["candidate"]["quality"] for r in group) / len(group)
        b = sum(r["baseline"]["quality"] for r in group) / len(group)
        candidate.append(a)
        baseline.append(b)
        differences.append(a - b)
    n = len(tasks)
    return {
        "samples": n,
        "quality_lower": lower_bound(candidate),
        "quality": sum(candidate) / n if n else None,
        "baseline_quality": sum(baseline) / n if n else None,
        "delta": sum(differences) / n if n else None,
        "delta_lower": (sum(differences) / n - math.sqrt(2 * math.log(20) / n)) if n else -1.0,
        "agreement": sum(r["agreement"] for r in records) / len(records) if records else None,
    }


def passes(stats, requirements):
    return (
        stats["samples"] >= requirements.min_samples
        and stats["quality_lower"] >= requirements.min_quality
        and stats["delta_lower"] >= -requirements.max_degradation
    )
