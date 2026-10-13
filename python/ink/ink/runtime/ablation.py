"""Deterministic A/B reporting for optional Small control-plane value."""

from __future__ import annotations

from typing import Any


def _arm_metrics(store, site_version: str) -> dict[str, Any]:
    rows = store.history(site_version)
    authority = store.authority_events(site_version)
    local = [row for row in rows if row["source"] == "fast_path"]
    correct = [
        row
        for row in local
        if row["outcome"] is not None and float(row["outcome"].get("quality", 0.0)) > 0.0
    ]

    def first_authority(engine: str):
        event = next(
            (
                item
                for item in authority
                if item["engine"] == engine and item["new_status"] == "ACTIVE"
            ),
            None,
        )
        return event["step"] if event else None

    def first_coverage(target: float):
        local_n = 0
        for index, row in enumerate(rows, 1):
            local_n += int(row["source"] == "fast_path")
            if local_n / index >= target:
                return index
        return None

    total = len(rows)
    return {
        "n_to_first_qualified_linear": first_authority("linear"),
        "n_to_first_qualified_exact": first_authority("exact"),
        "n_to_10pct_fast_path": first_coverage(0.10),
        "n_to_25pct_fast_path": first_coverage(0.25),
        "n_to_50pct_fast_path": first_coverage(0.50),
        "final_fast_path_coverage": len(local) / total if total else 0.0,
        "verified_local_accuracy": len(correct) / len(local) if local else None,
        "host_calls_avoided": len(local),
        "local_serves": len(local),
        "correct_local_serves": len(correct),
        "false_local_serves": len(local) - len(correct),
        "false_local_serve_rate": ((len(local) - len(correct)) / len(local) if local else None),
    }


def control_plane_ablation_report(
    control_store, small_store, control_site_version: str, small_site_version: str
) -> dict[str, Any]:
    """Compare already-run identical streams; caller owns stream/seed equality proof."""
    arm_a = _arm_metrics(control_store, control_site_version)
    arm_b = _arm_metrics(small_store, small_site_version)
    return {
        "arm_a": {"policy_model": None, **arm_a},
        "arm_b": {"policy_model": "small", **arm_b},
        "small_changed_product_metrics": any(
            arm_a[key] != arm_b[key]
            for key in arm_a
            if key not in {"local_serves", "correct_local_serves", "false_local_serves"}
        ),
    }
