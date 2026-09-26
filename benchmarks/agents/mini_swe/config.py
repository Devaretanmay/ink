"""
Mini-SWE-Agent configuration model.
Captures pinned model version, provider parameters, and budget limits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MiniSWEConfig:
    provider: str = "openai"
    model: str = "gpt-4o-2024-08-06"
    temperature: float = 0.0
    reasoning_effort: str = "medium"
    max_steps: int = 100
    max_tokens: int = 100_000
    max_wall_time_seconds: int = 1800
    docker_image: str = "swebench/swe-bench-verified:latest"
    seed: int = 42
    use_container: bool = True
