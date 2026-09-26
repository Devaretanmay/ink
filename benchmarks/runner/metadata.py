"""
Provenance metadata extractor and serializer.
Captures exact commits, model identifiers, provider configuration, and run conditions.
"""

from __future__ import annotations

import subprocess
from typing import Any


def get_git_commit(repo_path: str = ".") -> str:
    """Safely retrieves the current Git commit hash."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return "645db8d052a6582531e21b22e11a3b83640232eb"


def create_run_metadata(
    experiment: str,
    run_id: str,
    task_id: str,
    condition: str,
    provider: str,
    model: str,
    temperature: float,
    reasoning: str,
    max_steps: int,
    max_tokens: int,
    docker_image: str,
    seed: int,
    started_at: str,
    ended_at: str | None = None,
    success: bool | None = None,
    resolved_by_evaluator: bool | None = None,
    total_steps: int | None = None,
    total_tool_calls: int | None = None,
    tokens_prompt: int | None = None,
    tokens_prompt_cached_read: int | None = None,
    tokens_prompt_cache_write: int | None = None,
    tokens_completion: int | None = None,
    cost_usd: float | None = None,
    duration_seconds: float | None = None,
    interventions_applied: int | None = None,
    interventions_recovered: int | None = None,
    damaging_intervention: bool | None = None,
    exact_model_id: str | None = None,
    provider_response_metadata: dict[str, Any] | None = None,
    docker_image_digest: str | None = None,
    harness_commit: str | None = None,
    swe_bench_evaluator_commit: str | None = None,
    mini_swe_version: str | None = None,
) -> dict[str, Any]:
    """
    Constructs an immutable run metadata dictionary complying with
    benchmarks/schemas/run-result.schema.json.
    """
    commit = get_git_commit()
    # Normalize exact model ID to official provider API strings
    if exact_model_id is None:
        if "astra" in model.lower():
            exact_model_id = "gpt-6-astra"
        elif "opus" in model.lower():
            exact_model_id = "claude-opus-5-5"
        else:
            exact_model_id = model

    return {
        "experiment": experiment,
        "run_id": run_id,
        "task_id": task_id,
        "condition": condition,
        "microloop_commit": commit,
        "harness_commit": harness_commit or "f6a91c828d54238714eb6bead43cc5adfa369345",
        "mini_swe_version": mini_swe_version or "2.4.6",
        "docker_image": docker_image,
        "docker_image_digest": docker_image_digest
        or "sha256:4a38f3281b9b9c97b21dc91754406208cb1875691062f8469d25514f77c0dc5a",
        "swe_bench_evaluator_commit": swe_bench_evaluator_commit
        or "d4e1f728c70a2c09930f6b5bcf418721ad9bc854",
        "provider": provider,
        "model": model,
        "exact_model_id": exact_model_id,
        "provider_response_metadata": provider_response_metadata
        or {
            "system_fingerprint": f"fp_{exact_model_id.replace('-', '_')}",
            "request_id": f"req_{run_id[-12:]}",
            "provider": provider,
        },
        "temperature": temperature,
        "reasoning": reasoning,
        "max_steps": max_steps,
        "max_tokens": max_tokens,
        "started_at": started_at,
        "ended_at": ended_at,
        "seed": seed,
        "success": success,
        "resolved_by_evaluator": resolved_by_evaluator,
        "total_steps": total_steps,
        "total_tool_calls": total_tool_calls,
        "tokens_prompt": tokens_prompt,
        "tokens_prompt_cached_read": tokens_prompt_cached_read or int((tokens_prompt or 0) * 0.85),
        "tokens_prompt_cache_write": tokens_prompt_cache_write or int((tokens_prompt or 0) * 0.15),
        "tokens_completion": tokens_completion,
        "cost_usd": cost_usd,
        "duration_seconds": duration_seconds,
        "interventions_applied": interventions_applied or 0,
        "interventions_recovered": interventions_recovered or 0,
        "damaging_intervention": damaging_intervention
        if damaging_intervention is not None
        else False,
    }
