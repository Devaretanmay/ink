"""
Mini-SWE-Agent configuration model.
Captures pinned model version, provider parameters, and budget limits.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MiniSWEConfig:
    provider: str = "openai"
    model: str = "gpt-6-astra"
    temperature: float = 0.0
    reasoning_effort: str = "medium"
    max_steps: int = 100
    max_tokens: int = 100_000
    max_wall_time_seconds: int = 1800
    docker_image: str = "swebench/swe-bench-verified:latest"
    docker_image_digest: str = (
        "sha256:4a38f3281b9b9c97b21dc91754406208cb1875691062f8469d25514f77c0dc5a"
    )
    harness_commit: str = "f6a91c828d54238714eb6bead43cc5adfa369345"
    mini_swe_version: str = "2.4.6"
    swe_bench_evaluator_commit: str = "d4e1f728c70a2c09930f6b5bcf418721ad9bc854"
    seed: int = 42
    use_container: bool = True

    def get_pricing(self) -> dict[str, float]:
        """
        Returns USD per 1M tokens:
        - uncached_prompt: input token cost without cache hit
        - cached_prompt: input token read cost with cache hit
        - cache_write: prompt token cost to write/create cache entry
        - completion: output / completion token cost
        """
        m = self.model.lower()
        if "gpt-6-astra" in m:
            # Official OpenAI GPT-6 Astra pricing:
            # $10.00/1M input, $1.00/1M cached input, $12.50/1M cache write, $50.00/1M output
            return {
                "uncached_prompt": 10.00,
                "cached_prompt": 1.00,
                "cache_write": 12.50,
                "completion": 50.00,
            }
        elif "opus-5" in m or "claude-opus-5-5" in m:
            # Official Anthropic Claude Opus 5.5 pricing:
            # $15.00/1M input, $1.50/1M cached input, $18.75/1M cache write, $75.00/1M output
            return {
                "uncached_prompt": 15.00,
                "cached_prompt": 1.50,
                "cache_write": 18.75,
                "completion": 75.00,
            }
        elif "claude" in m or "sonnet-5" in m:
            return {
                "uncached_prompt": 3.00,
                "cached_prompt": 0.30,
                "cache_write": 3.75,
                "completion": 15.00,
            }
        elif "deepseek" in m:
            return {
                "uncached_prompt": 0.14,
                "cached_prompt": 0.014,
                "cache_write": 0.14,
                "completion": 0.28,
            }
        else:
            return {
                "uncached_prompt": 10.00,
                "cached_prompt": 1.00,
                "cache_write": 12.50,
                "completion": 50.00,
            }
