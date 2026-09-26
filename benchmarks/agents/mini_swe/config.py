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
    model: str = "claude-opus-5-5-20260922"
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
        if "opus-5" in m:
            # Anthropic Claude Opus 5.5 (90% prompt caching discount)
            return {"uncached_prompt": 4.00, "cached_prompt": 0.40, "completion": 18.00}
        elif "claude" in m or "sonnet-5" in m or "fable-5" in m:
            # Anthropic Claude 5 Family (90% discount)
            return {"uncached_prompt": 2.50, "cached_prompt": 0.25, "completion": 12.00}
        elif "gpt-6-astra" in m:
            # OpenAI GPT-6 Astra flagship (50% automatic caching discount)
            return {"uncached_prompt": 3.50, "cached_prompt": 1.75, "completion": 14.00}
        elif "gpt-6" in m or "sol" in m:
            # OpenAI GPT-6 Sol / Luna balanced
            return {"uncached_prompt": 1.50, "cached_prompt": 0.75, "completion": 6.00}
        elif "deepseek-v4" in m or "deepseek" in m:
            # DeepSeek V4.1 Flash architecture
            return {"uncached_prompt": 0.10, "cached_prompt": 0.01, "completion": 0.20}
        else: # default fallback
            return {"uncached_prompt": 2.50, "cached_prompt": 1.25, "completion": 10.00}
