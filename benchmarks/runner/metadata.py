"""
Provenance metadata extractor and serializer.
Captures exact commits, model identifiers, provider configuration, and run conditions.
"""
from __future__ import annotations

import datetime
import subprocess
from typing import Any, Dict, Optional


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
    return "unknown_commit"


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
    ended_at: Optional[str] = None,
    success: Optional[bool] = None,
    resolved_by_evaluator: Optional[bool] = None,
    total_steps: Optional[int] = None,
    total_tool_calls: Optional[int] = None,
    tokens_prompt: Optional[int] = None,
    tokens_completion: Optional[int] = None,
    cost_usd: Optional[float] = None,
    duration_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Constructs a metadata dictionary complying with result.schema.json.
    """
    commit = get_git_commit()
    return {
        "experiment": experiment,
        "run_id": run_id,
        "task_id": task_id,
        "condition": condition,
        "microloop_commit": commit,
        "harness_commit": commit,
        "provider": provider,
        "model": model,
        "temperature": temperature,
        "reasoning": reasoning,
        "max_steps": max_steps,
        "max_tokens": max_tokens,
        "started_at": started_at,
        "ended_at": ended_at,
        "docker_image": docker_image,
        "seed": seed,
        "success": success,
        "resolved_by_evaluator": resolved_by_evaluator,
        "total_steps": total_steps,
        "total_tool_calls": total_tool_calls,
        "tokens_prompt": tokens_prompt,
        "tokens_completion": tokens_completion,
        "cost_usd": cost_usd,
        "duration_seconds": duration_seconds,
    }
