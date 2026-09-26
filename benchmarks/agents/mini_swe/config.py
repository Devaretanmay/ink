"""
Mini-SWE-Agent configuration model.
Captures pinned model version, provider parameters, and budget limits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class MiniSWEConfig:
    provider: str = "anthropic"
    model: str = "claude-3-7-sonnet-20250219"
    temperature: float = 0.0
    reasoning_effort: str = "medium"
    max_steps: int = 100
    max_tokens: int = 100_000
    max_wall_time_seconds: int = 1800
    docker_image: str = "swebench/swe-bench-verified:latest"
    seed: int = 42
    use_container: bool = True

    def get_pricing(self) -> Dict[str, float]:
        """Returns (uncached_prompt, cached_prompt, completion) price in USD per 1M tokens."""
        m = self.model.lower()
        if "claude" in m:
            # Anthropic 90% prompt caching discount
            return {"uncached_prompt": 3.00, "cached_prompt": 0.30, "completion": 15.00}
        elif "o3-mini" in m:
            # OpenAI o3-mini 50% automatic caching discount
            return {"uncached_prompt": 1.10, "cached_prompt": 0.55, "completion": 4.40}
        elif "o1" in m or "o3" in m:
            return {"uncached_prompt": 15.00, "cached_prompt": 7.50, "completion": 60.00}
        elif "deepseek-r1" in m:
            return {"uncached_prompt": 0.55, "cached_prompt": 0.14, "completion": 2.19}
        elif "deepseek" in m:
            return {"uncached_prompt": 0.14, "cached_prompt": 0.014, "completion": 0.28}
        else: # default gpt-4o
            return {"uncached_prompt": 2.50, "cached_prompt": 1.25, "completion": 10.00}
