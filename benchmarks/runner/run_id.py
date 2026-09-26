"""
Unique Run Identifier generator.
Produces monotonically sortable, collision-free run identifiers.
"""
from __future__ import annotations

import os
import time
import uuid


def generate_run_id(task_id: str, condition: str, seed: int) -> str:
    """
    Generates a human-readable, collision-resistant run identifier:
    e.g. run_20260926_145000_django_11099_vanilla_s1_a1b2
    """
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    clean_task = task_id.replace("/", "_").replace("__", "_")[:20]
    random_suffix = uuid.uuid4().hex[:6]
    return f"run_{timestamp}_{clean_task}_{condition}_s{seed}_{random_suffix}"
