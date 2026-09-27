"""Evidence calculations shared by calibration, evaluation, and drift checks."""

from __future__ import annotations

import math
from dataclasses import asdict

from .contracts import Outcome, canonical, digest


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
    for row in rows:
        if row["outcome"] is None:
            continue
        key = canonical(row["state"])
        if key not in payload["coverage"]:
            continue
        choice, raw = engine.predict(payload["engine_data"], row["state"])
        if choice not in payload["choices"]:
            raise ValueError("Engine returned an undeclared choice")
        # Verify both actions in the same replay environment; never transfer a factual
        # fallback outcome to an unexecuted candidate, including when they agree.
        candidate = verifier(row["state"], choice)
        baseline = verifier(row["state"], row["choice"])
        if not isinstance(candidate, Outcome) or not isinstance(baseline, Outcome):
            raise TypeError("Verifier must return Outcome with independent evidence")
        identity = (candidate.verifier, candidate.verifier_version)
        if identity != (baseline.verifier, baseline.verifier_version):
            raise ValueError("Verifier identity changed during replay")
        results.append(
            {
                "id": row["id"],
                "task": row["task"],
                "region": key,
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


def profile_id(profile):
    return digest(profile)
