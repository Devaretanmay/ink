"""
Mini-SWE-Agent Minimal Coding Agent Harness.
Provides a minimal, reproducible agent execution loop with 4 tools:
shell, read_file, write_file, search_files.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolResult:
    success: bool
    output: str
    error: Optional[str] = None
    output_hash: str = ""


class CodingAgentHarness:
    """Minimal agent environment for controlled benchmark evaluation."""

    def __init__(self, workspace_root: str, max_steps: int = 50) -> None:
        self.workspace_root = os.path.abspath(workspace_root)
        self.max_steps = max_steps
        self.step_count = 0
        self.history: List[Dict[str, Any]] = []

    def execute_shell(self, command: str, timeout_seconds: int = 60) -> ToolResult:
        """Executes a bash shell command in the workspace directory."""
        try:
            res = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            success = res.returncode == 0
            output = res.stdout if success else res.stderr
            return ToolResult(success=success, output=output.strip())
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                output="",
                error=f"Command timed out after {timeout_seconds}s",
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

    def read_file(
        self, relative_path: str, start_line: int = 1, end_line: int = 200
    ) -> ToolResult:
        """Reads a line range from a file in the workspace."""
        full_path = os.path.join(self.workspace_root, relative_path)
        if not os.path.exists(full_path):
            return ToolResult(
                success=False,
                output="",
                error=f"File not found: {relative_path}",
            )
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            start = max(1, start_line) - 1
            end = min(len(lines), end_line)
            content = "".join(lines[start:end])
            return ToolResult(success=True, output=content)
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

    def write_file(self, relative_path: str, content: str) -> ToolResult:
        """Writes content to a file in the workspace."""
        full_path = os.path.join(self.workspace_root, relative_path)
        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return ToolResult(
                success=True, output=f"Successfully wrote {len(content)} bytes to {relative_path}"
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

    def search_files(self, pattern: str, subpath: str = ".") -> ToolResult:
        """Searches for a text pattern or file across the workspace."""
        return self.execute_shell(f"grep -rn '{pattern}' {subpath} | head -n 50")
