"""
Result Writer for separating raw telemetry and Microloop-derived features.
Manages per-run directory artifacts:
results/<run_id>/
  metadata.json
  trajectory.jsonl
  final.patch
  evaluation.json
  microloop_features.jsonl
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional


class ResultWriter:
    def __init__(self, base_output_dir: str = "benchmarks/results/raw") -> None:
        self.base_output_dir = os.path.abspath(base_output_dir)
        os.makedirs(self.base_output_dir, exist_ok=True)

    def get_run_dir(self, run_id: str) -> str:
        run_dir = os.path.join(self.base_output_dir, run_id)
        os.makedirs(run_dir, exist_ok=True)
        return run_dir

    def write_run_bundle(
        self,
        run_id: str,
        metadata: Dict[str, Any],
        trajectory_events: List[Dict[str, Any]],
        final_patch: str,
        evaluation_result: Dict[str, Any],
        microloop_features: List[Dict[str, Any]],
    ) -> str:
        """
        Persists all artifacts for an individual trial into its dedicated directory.
        """
        run_dir = self.get_run_dir(run_id)

        # 1. Write metadata.json (provenance)
        metadata_path = os.path.join(run_dir, "metadata.json")
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        # 2. Write raw trajectory.jsonl (one JSON event per line)
        traj_path = os.path.join(run_dir, "trajectory.jsonl")
        with open(traj_path, "w", encoding="utf-8") as f:
            for event in trajectory_events:
                f.write(json.dumps(event) + "\n")

        # 3. Write final.patch (git diff)
        patch_path = os.path.join(run_dir, "final.patch")
        with open(patch_path, "w", encoding="utf-8") as f:
            f.write(final_patch)

        # 4. Write evaluation.json (official ground-truth score)
        eval_path = os.path.join(run_dir, "evaluation.json")
        with open(eval_path, "w", encoding="utf-8") as f:
            json.dump(evaluation_result, f, indent=2)

        # 5. Write derived microloop_features.jsonl
        features_path = os.path.join(run_dir, "microloop_features.jsonl")
        with open(features_path, "w", encoding="utf-8") as f:
            for feat in microloop_features:
                f.write(json.dumps(feat) + "\n")

        return run_dir
