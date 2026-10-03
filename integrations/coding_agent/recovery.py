from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ContextPatch:
    reason: str
    question: str
    evidence: list[dict[str, Any]]
    token_count: int


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class LocalRetrievalAdapter:
    def __init__(self, repo_path: Path | str) -> None:
        self.repo_path = Path(repo_path).resolve()
        self._rg_path = shutil.which("rg")

    def _run_cmd(self, cmd: list[str]) -> str:
        try:
            res = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            return res.stdout
        except (subprocess.SubprocessError, OSError):
            return ""

    def search_source(self, pattern: str, max_matches: int = 5) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if self._rg_path:
            cmd = [
                self._rg_path,
                "-n",
                "-m",
                str(max_matches),
                "-g",
                "!*test*",
                "-g",
                "!.*",
                pattern,
            ]
            out = self._run_cmd(cmd)
            for line in out.splitlines()[:max_matches]:
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    line_num = int(parts[1]) if parts[1].isdigit() else 1
                    results.append({
                        "source": "source",
                        "path": parts[0],
                        "line": line_num,
                        "content": parts[2].strip(),
                    })
        if not results:
            try:
                rx = re.compile(pattern, re.IGNORECASE)
                for p in self.repo_path.rglob("*.py"):
                    if "test" in p.name or any(part.startswith(".") for part in p.parts):
                        continue
                    try:
                        lines = p.read_text(errors="ignore").splitlines()
                        for i, line in enumerate(lines, 1):
                            if rx.search(line):
                                rel = str(p.relative_to(self.repo_path))
                                results.append({
                                    "source": "source",
                                    "path": rel,
                                    "line": i,
                                    "content": line.strip(),
                                })
                                if len(results) >= max_matches:
                                    return results
                    except OSError:
                        pass
            except re.error:
                pass
        return results

    def search_tests(self, pattern: str, max_matches: int = 5) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if self._rg_path:
            cmd = [self._rg_path, "-n", "-m", str(max_matches), "-g", "*test*", pattern]
            out = self._run_cmd(cmd)
            for line in out.splitlines()[:max_matches]:
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    line_num = int(parts[1]) if parts[1].isdigit() else 1
                    results.append({
                        "source": "tests",
                        "path": parts[0],
                        "line": line_num,
                        "content": parts[2].strip(),
                    })
        if not results:
            try:
                rx = re.compile(pattern, re.IGNORECASE)
                for p in self.repo_path.rglob("*test*.py"):
                    try:
                        lines = p.read_text(errors="ignore").splitlines()
                        for i, line in enumerate(lines, 1):
                            if rx.search(line):
                                rel = str(p.relative_to(self.repo_path))
                                results.append({
                                    "source": "tests",
                                    "path": rel,
                                    "line": i,
                                    "content": line.strip(),
                                })
                                if len(results) >= max_matches:
                                    return results
                    except OSError:
                        pass
            except re.error:
                pass
        return results

    def search_git_history(self, query: str, max_commits: int = 5) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        out = self._run_cmd(["git", "log", "-n", str(max_commits), "--grep", query, "--oneline"])
        for line in out.splitlines()[:max_commits]:
            parts = line.strip().split(" ", 1)
            if parts:
                subj = parts[1] if len(parts) > 1 else ""
                results.append({"source": "git_history", "commit": parts[0], "subject": subj})
        return results

    def retrieve(
        self,
        question: str,
        query: str,
        reason: str = "recovery_needed",
        max_tokens: int = 1500,
    ) -> ContextPatch:
        evidence: list[dict[str, Any]] = []
        curr_tokens = estimate_tokens(question)

        for m in self.search_source(query, max_matches=4):
            tok = estimate_tokens(str(m))
            if curr_tokens + tok <= max_tokens:
                evidence.append(m)
                curr_tokens += tok

        for m in self.search_tests(query, max_matches=3):
            tok = estimate_tokens(str(m))
            if curr_tokens + tok <= max_tokens:
                evidence.append(m)
                curr_tokens += tok

        for m in self.search_git_history(query, max_commits=3):
            tok = estimate_tokens(str(m))
            if curr_tokens + tok <= max_tokens:
                evidence.append(m)
                curr_tokens += tok

        return ContextPatch(
            reason=reason,
            question=question,
            evidence=evidence,
            token_count=curr_tokens,
        )
