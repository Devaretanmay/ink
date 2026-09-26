"""
Baseline B: Retry Baseline Condition.
Executes the base agent with a naive retry / restart policy:
- If an action fails with non-zero exit code: retry once.
- If step context is exhausted or error persists: restart from clean workspace once.
"""

from __future__ import annotations


class RetryPolicy:
    def __init__(self, max_restarts: int = 1, max_retries_per_error: int = 1) -> None:
        self.max_restarts = max_restarts
        self.max_retries_per_error = max_retries_per_error
        self.restarts_used = 0
        self.retries_used = 0

    def should_retry(self, success: bool) -> bool:
        if not success and self.retries_used < self.max_retries_per_error:
            self.retries_used += 1
            return True
        return False

    def should_restart(self, step: int, max_steps: int) -> bool:
        if step >= max_steps // 2 and self.restarts_used < self.max_restarts:
            self.restarts_used += 1
            return True
        return False
