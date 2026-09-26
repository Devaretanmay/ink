"""
Canonical event builder and parser for Mini-SWE-Agent trajectories.
Normalizes tool actions, observations, workspace state, and test metrics.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Dict, Optional


def hash_text(text: str) -> str:
    """Computes a stable Blake3 or SHA256 hex digest for opaque state tracking."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def parse_pytest_metrics(output: str) -> Dict[str, Optional[int]]:
    """
    Carefully parses test metrics from test runner output.
    Returns null if unknown. Never invents progress metrics.
    """
    passed = None
    failed = None

    # Matches patterns like "5 passed, 2 failed in 0.42s"
    m_fail = re.search(r"(\d+)\s+failed", output)
    if m_fail:
        failed = int(m_fail.group(1))

    m_pass = re.search(r"(\d+)\s+passed", output)
    if m_pass:
        passed = int(m_pass.group(1))

    # Also handle unittest syntax: "FAILED (failures=2, errors=1)"
    m_unit_fail = re.search(r"FAILED\s+\((?:failures=(\d+))?(?:,\s*)?(?:errors=(\d+))?\)", output)
    if m_unit_fail:
        f_count = int(m_unit_fail.group(1) or 0)
        e_count = int(m_unit_fail.group(2) or 0)
        failed = f_count + e_count

    return {
        "tests_passed": passed,
        "tests_failed": failed,
    }


def build_canonical_event(
    run_id: str,
    task_id: str,
    step: int,
    action_type: str,
    command: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    duration_ms: int,
    git_head: str = "HEAD",
    dirty: bool = False,
    changed_files: int = 0,
    diff_content: str = "",
    error_class: Optional[str] = None,
    verification_scope: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Constructs a schema-valid canonical event matching event.schema.json.
    """
    test_metrics = {"tests_passed": None, "tests_failed": None}
    if "pytest" in command or "test" in command or "unittest" in command:
        combined_output = stdout + "\n" + stderr
        parsed = parse_pytest_metrics(combined_output)
        if parsed["tests_failed"] is not None or parsed["tests_passed"] is not None:
            test_metrics = parsed
            if verification_scope is None:
                verification_scope = "pytest"

    diff_hash = hash_text(diff_content) if diff_content else "empty"

    return {
        "schema_version": "0.1",
        "run_id": run_id,
        "task_id": task_id,
        "step": step,
        "timestamp_ms": int(time.time() * 1000),
        "action": {
            "type": action_type,
            "command": command,
            "path": None,
        },
        "observation": {
            "exit_code": exit_code,
            "duration_ms": max(0, duration_ms),
            "stdout": stdout,
            "stderr": stderr,
            "error_class": error_class,
        },
        "workspace": {
            "git_head": git_head,
            "dirty": dirty,
            "changed_files": changed_files,
            "diff_hash": diff_hash,
        },
        "metrics": {
            "tests_passed": test_metrics["tests_passed"],
            "tests_failed": test_metrics["tests_failed"],
            "verification_scope": verification_scope,
        },
    }
