from __future__ import annotations

import collections
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ink import Ink

from .adapter import CodingAgentRecoveryAdapter
from .events import AgentEvent
from .features import extract_trajectory_features
from .opportunity import EVAL_WINDOW, OpportunityDetector

GROQ_API_BASE = "https://api.groq.com/openai/v1"
PREFERRED_MODELS = ("qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b")
DISALLOWED_COMMAND_PATTERNS = (
    r"\bgit\s+push\b",
    r"\bsudo\b",
    r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f\s+/",
    r"\bgit\s+reset\s+--hard\b",
    r"\.ssh\b",
    r"\.gnupg\b",
    r"\.aws\b",
    r"id_rsa",
    r"id_ed25519",
)

# The alpha sandbox has a fixed environment. Package installation cannot succeed
# and only consumes the bounded tool-action budget, so it is refused up front.
NOOP_INSTALL_PATTERNS = (
    r"\bpip3?\s+install\b",
    r"\bpython3?\s+-m\s+pip\b",
    r"\bconda\s+install\b",
    r"\bpoetry\s+add\b",
    r"\buv\s+pip\s+install\b",
    r"\bnpm\s+(install|i)\b",
    r"\bapt(-get)?\s+install\b",
    r"\bbrew\s+install\b",
)


def get_api_key() -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set")
    return key


def http_request(
    endpoint: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 60.0,
    max_retries: int = 10,
) -> dict[str, Any]:
    key = get_api_key()
    url = f"{GROQ_API_BASE}{endpoint}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "ink-alpha/0.6.0",
    }
    for attempt in range(max_retries):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_msg = exc.read().decode("utf-8", errors="ignore")
            if exc.code == 429 and attempt < max_retries - 1:
                match = re.search(r"try again in (?:(\d+)m)?([\d\.]+)s", err_msg)
                if match:
                    mins = float(match.group(1)) if match.group(1) else 0.0
                    secs = float(match.group(2))
                    wait_sec = mins * 60.0 + secs + 1.0
                else:
                    wait_sec = 3.0
                msg = (
                    f"[Groq Rate Limit] Sleeping {wait_sec:.1f}s before retry "
                    f"(attempt {attempt+1}/{max_retries})..."
                )
                print(msg)
                time.sleep(wait_sec)
                continue
            raise RuntimeError(f"Groq API error HTTP {exc.code}: {err_msg}") from None
        except urllib.error.URLError as exc:
            if attempt < max_retries - 1:
                time.sleep(2.0)
                continue
            raise RuntimeError(f"Groq API network error: {exc.reason}") from None
    raise RuntimeError("Max retries exceeded for Groq API")


def query_models() -> list[str]:
    data = http_request("/models", method="GET")
    return [m["id"] for m in data.get("data", [])]


def select_model(
    available_models: list[str],
    probe: Callable[[str], None] | None = None,
) -> str:
    def default_probe(model: str) -> None:
        http_request(
            "/chat/completions",
            method="POST",
            payload={
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
            timeout=10.0,
            max_retries=1,
        )

    check = probe or default_probe

    for pref in PREFERRED_MODELS:
        if pref in available_models:
            try:
                check(pref)
                return pref
            except RuntimeError as exc:
                err_str = str(exc).lower()
                if (
                    "tokens per day" in err_str
                    or "daily" in err_str
                    or "tpd" in err_str
                    or "rate_limit_exceeded" in err_str
                ):
                    msg = (
                        f"[Model Selection] {pref} quota/limit exhausted on Groq; "
                        "falling back to next preferred model..."
                    )
                    print(msg)
                    continue
                raise
    for m in available_models:
        if "openai" in m.lower() or "llama" in m.lower():
            return m
    if available_models:
        return available_models[0]
    raise RuntimeError("No models returned by Groq API")


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory relative to the repository root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Subdirectory path, or '.' for root (can also use 'path').",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Alternative parameter for subdirectory path or '.' for root."
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read content of a file with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to repository root.",
                    },
                    "start_line": {"type": "integer", "description": "Starting line (1-indexed)."},
                    "end_line": {"type": "integer", "description": "Ending line inclusive."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search code using ripgrep or regex pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search pattern or symbol name."},
                    "path_pattern": {
                        "type": "string",
                        "description": "Optional file glob e.g. '*.py'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Inspect recent git commits on current branch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_count": {
                        "type": "integer",
                        "description": "Number of commits to return.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Inspect current uncommitted workspace changes.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run pytest or test command and return exit code and test counts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "test_command": {
                        "type": "string",
                        "description": "Test command, e.g. 'pytest tests/test_foo.py'.",
                    }
                },
                "required": ["test_command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a non-destructive shell command in the repository directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute."}
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact target string with replacement in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path."},
                    "old_content": {
                        "type": "string",
                        "description": "Exact text block to replace.",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "New replacement text block.",
                    },
                },
                "required": ["path", "old_content", "new_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Declare that the task is completed and summarize the resolution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Summary of the bug fix."}
                },
                "required": ["summary"],
            },
        },
    },
]


class SafeToolExecutor:
    def __init__(self, worktree_dir: Path) -> None:
        self.worktree_dir = worktree_dir.resolve()

    def _check_safe_command(self, cmd: str) -> None:
        for pat in DISALLOWED_COMMAND_PATTERNS:
            if re.search(pat, cmd, re.IGNORECASE):
                raise PermissionError(f"Command forbidden by safety boundary: {cmd!r}")

    def _is_noop_install(self, cmd: str) -> bool:
        return any(re.search(pat, cmd, re.IGNORECASE) for pat in NOOP_INSTALL_PATTERNS)

    def execute(self, tool_name: str, args: dict[str, Any]) -> tuple[str, AgentEvent]:
        start = time.time()
        if tool_name == "list_files":
            dir_str = args.get("directory") or args.get("path") or "."
            d = self.worktree_dir / dir_str
            try:
                files = [
                    str(p.relative_to(self.worktree_dir))
                    for p in d.rglob("*")
                    if not any(part.startswith(".") for part in p.parts)
                ][:100]
                res_str = "\n".join(files) if files else "empty directory"
                return res_str, AgentEvent(
                    "file_search",
                    timestamp=start,
                    query="list_files",
                    details={"match_count": len(files)},
                )
            except Exception as e:
                return f"Error listing files: {e}", AgentEvent(
                    "tool_error", timestamp=start, error=str(e)
                )

        if tool_name == "read_file":
            path_str = args.get("path") or args.get("file_path") or args.get("filename") or ""
            p = self.worktree_dir / path_str
            try:
                lines = p.read_text(errors="ignore").splitlines()
                s = max(1, args.get("start_line", 1)) - 1
                e = args.get("end_line", len(lines))
                sliced = [f"{i+1}: {line}" for i, line in enumerate(lines[s:e], start=s)]
                bucket = f"{(s + 1) // 50}:{e // 50}"
                return "\n".join(sliced), AgentEvent(
                    "file_read",
                    timestamp=start,
                    path=path_str,
                    details={"line_bucket": bucket, "match_count": 1},
                )
            except Exception as e:
                return f"Error reading {path_str}: {e}", AgentEvent(
                    "tool_error", timestamp=start, error=str(e), path=path_str
                )

        if tool_name == "search_code":
            q = args.get("query") or args.get("pattern") or args.get("q") or ""
            cmd = ["rg", "-n", "-m", "10", q]
            try:
                res = subprocess.run(
                    cmd,
                    cwd=self.worktree_dir,
                    capture_output=True,
                    text=True,
                    timeout=10.0,
                )
                out = res.stdout if res.stdout else "No matches found."
                match_count = len(res.stdout.splitlines()) if res.stdout else 0
                return out, AgentEvent(
                    "file_search",
                    timestamp=start,
                    query=q,
                    details={"match_count": match_count},
                )
            except Exception as e:
                return f"Error searching code: {e}", AgentEvent(
                    "tool_error", timestamp=start, error=str(e), query=q
                )

        if tool_name == "git_log":
            n = args.get("max_count", 5)
            try:
                res = subprocess.run(
                    ["git", "log", "-n", str(n), "--oneline"],
                    cwd=self.worktree_dir,
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                )
                return res.stdout, AgentEvent(
                    "command_run", timestamp=start, command=f"git log -n {n}"
                )
            except Exception as e:
                return f"Error running git log: {e}", AgentEvent(
                    "tool_error", timestamp=start, error=str(e)
                )

        if tool_name == "git_diff":
            try:
                res = subprocess.run(
                    ["git", "diff"],
                    cwd=self.worktree_dir,
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                )
                return (
                    res.stdout if res.stdout else "No uncommitted diff.",
                    AgentEvent("command_run", timestamp=start, command="git diff"),
                )
            except Exception as e:
                return f"Error running git diff: {e}", AgentEvent(
                    "tool_error", timestamp=start, error=str(e)
                )

        if tool_name == "run_tests":
            cmd_str = args["test_command"]
            self._check_safe_command(cmd_str)
            if self._is_noop_install(cmd_str):
                msg = (
                    "Package installation is unavailable in this environment. "
                    "Do not install dependencies; fix the source file named in the bug report."
                )
                return msg, AgentEvent(
                    "command_failed", timestamp=start, command=cmd_str, error=msg
                )
            try:
                res = subprocess.run(
                    cmd_str,
                    shell=True,
                    cwd=self.worktree_dir,
                    capture_output=True,
                    text=True,
                    timeout=60.0,
                )
                combined = (res.stdout + "\n" + res.stderr).strip()
                passed = len(re.findall(r"\bPASSED\b|\bpassed\b", combined))
                failed = len(re.findall(r"\bFAILED\b|\bfailed\b|\bERROR\b|\berror\b", combined))
                t_counts = {"passed": passed, "failed": failed}
                if res.returncode == 0:
                    return combined, AgentEvent(
                        "test_passed", timestamp=start, command=cmd_str, test_counts=t_counts
                    )
                return combined, AgentEvent(
                    "test_failed",
                    timestamp=start,
                    command=cmd_str,
                    error=combined[:100],
                    test_counts=t_counts,
                )
            except Exception as e:
                return f"Error executing tests: {e}", AgentEvent(
                    "command_failed", timestamp=start, command=cmd_str, error=str(e)
                )

        if tool_name == "run_command":
            cmd_str = args["command"]
            self._check_safe_command(cmd_str)
            if self._is_noop_install(cmd_str):
                msg = (
                    "Package installation is unavailable in this environment. "
                    "Do not install dependencies; fix the source file named in the bug report."
                )
                return msg, AgentEvent(
                    "command_failed", timestamp=start, command=cmd_str, error=msg
                )
            try:
                res = subprocess.run(
                    cmd_str,
                    shell=True,
                    cwd=self.worktree_dir,
                    capture_output=True,
                    text=True,
                    timeout=30.0,
                )
                combined = (res.stdout + "\n" + res.stderr).strip()
                if res.returncode == 0:
                    return combined, AgentEvent(
                        "command_run", timestamp=start, command=cmd_str
                    )
                return combined, AgentEvent(
                    "command_failed", timestamp=start, command=cmd_str, error=combined[:100]
                )
            except Exception as e:
                return f"Error running command: {e}", AgentEvent(
                    "command_failed", timestamp=start, command=cmd_str, error=str(e)
                )

        if tool_name == "edit_file":
            path_str = args["path"]
            old_c = args["old_content"]
            new_c = args["new_content"]
            p = self.worktree_dir / path_str
            try:
                content = p.read_text()
                if old_c not in content:
                    return f"Error: old_content not found in {path_str}", AgentEvent(
                        "tool_error", timestamp=start, path=path_str, error="target_not_found"
                    )
                updated = content.replace(old_c, new_c, 1)
                p.write_text(updated)
                return f"Successfully edited {path_str}", AgentEvent(
                    "file_edit", timestamp=start, path=path_str
                )
            except Exception as e:
                return f"Error editing {path_str}: {e}", AgentEvent(
                    "tool_error", timestamp=start, path=path_str, error=str(e)
                )

        if tool_name == "finish":
            summary = args.get("summary", "")
            return f"Task finished: {summary}", AgentEvent(
                "tool_call",
                timestamp=start,
                tool_name="finish",
                details={"task_completed": True, "summary": summary},
            )

        return f"Unknown tool: {tool_name}", AgentEvent(
            "tool_error", timestamp=start, error=f"Unknown tool: {tool_name}"
        )


@dataclass
class LiveTaskExecutionResult:
    task_id: str
    condition: str
    completed: bool
    model_id: str
    model_calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    wall_clock_sec: float
    tool_calls: int
    searches: int
    repeated_actions: int
    failed_commands: int
    tests_run: int
    ink_decisions: int = 0
    local_fast_path_serves: int = 0
    learned_model_candidates: int = 0
    fallbacks: int = 0
    context_retrievals: int = 0
    context_patch_tokens: int = 0
    false_interventions: int = 0
    provider_latency_sec: float = 0.0
    tool_latency_sec: float = 0.0
    ink_latency_sec: float = 0.0
    opportunities: int = 0
    opportunity_reasons: dict[str, int] = field(default_factory=dict)
    teacher_action_counts: dict[str, int] = field(default_factory=dict)
    diff: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    raw_trajectory_excerpt: str = ""


def prune_messages(messages: list[dict[str, Any]], max_tokens: int = 3500) -> list[dict[str, Any]]:
    if len(messages) <= 4:
        return messages
    header = messages[:2]
    rest = list(messages[2:])

    def est_toks(msgs: list[dict[str, Any]]) -> int:
        return sum(len(json.dumps(m)) // 4 for m in msgs)

    while len(rest) > 2 and est_toks(header + rest) > max_tokens:
        if rest[0].get("tool_calls"):
            call_ids = {tc.get("id") for tc in rest[0].get("tool_calls", [])}
            rest.pop(0)
            while (
                rest
                and rest[0].get("role") == "tool"
                and rest[0].get("tool_call_id") in call_ids
            ):
                rest.pop(0)
        else:
            rest.pop(0)
    return header + rest


class LiveGroqCodingAgent:
    def __init__(
        self,
        worktree_dir: Path,
        model_id: str | None = None,
        reasoning_effort: str = "medium",
        max_tool_actions: int = 40,
        timeout_sec: float = 900.0,
    ) -> None:
        self.worktree_dir = worktree_dir.resolve()
        self.executor = SafeToolExecutor(self.worktree_dir)
        self.reasoning_effort = reasoning_effort
        self.max_tool_actions = max_tool_actions
        self.timeout_sec = timeout_sec

        if model_id is None:
            available = query_models()
            self.model_id = select_model(available)
        else:
            self.model_id = model_id

    def run_task(
        self,
        task_id: str,
        issue_description: str,
        condition: str = "A_agent_alone",
        ink_client: Ink | None = None,
        observe_only: bool = True,
    ) -> LiveTaskExecutionResult:
        started = time.time()
        adapter: CodingAgentRecoveryAdapter | None = None
        detector: OpportunityDetector | None = None
        if condition in ("B_groq_ink", "B_observe") and ink_client is not None:
            adapter = CodingAgentRecoveryAdapter(ink_client, repo_path=self.worktree_dir)
            detector = OpportunityDetector()

        system_prompt = (
            "You are an autonomous expert software engineering coding agent.\n"
            "You are given an open bug report in a real repository.\n"
            "Your job is to inspect the code, reproduce the bug, edit the source code to fix the "
            "root cause, verify with tests, and call finish() when verified.\n"
            "Rules:\n"
            "- Always use the provided tools. Never guess code without reading it.\n"
            "- The bug report names the file and function to change. Read that file, then make "
            "the smallest edit that fixes the root cause.\n"
            "- Do NOT install packages, create stub modules, or modify tests. The environment "
            "is fixed. If a dependency import fails, it is not part of your task.\n"
            "- Run the named test file (not the whole suite) to verify your fix.\n"
            "- If two consecutive commands fail the same way, change your approach instead of "
            "retrying.\n"
            "- Call finish() as soon as the target test passes."
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Task: {task_id}\n\nBug Report:\n{issue_description}"},
        ]

        model_calls = 0
        total_inp_tokens = 0
        total_out_tokens = 0
        tool_call_count = 0
        searches = 0
        failed_commands = 0
        tests_run = 0
        completed = False
        trajectory_log: list[str] = []
        ml_decisions = 0
        ml_serves = 0
        ml_fallbacks = 0
        ml_retrievals = 0
        ml_patch_tokens = 0
        recorded_events: list[AgentEvent] = []
        pending_decisions: list[tuple[str, int]] = []
        recent_signatures: collections.deque[str] = collections.deque(maxlen=4)
        edits_made = 0
        nudged_no_edit = False
        provider_latency = 0.0
        tool_latency = 0.0
        ml_latency = 0.0
        opportunities: list[dict[str, Any]] = []
        teacher_counts: dict[str, int] = {}

        while (
            tool_call_count < self.max_tool_actions
            and (time.time() - started) < self.timeout_sec
        ):
            time.sleep(1.0)
            model_calls += 1
            pruned_msgs = prune_messages(messages, max_tokens=3200)
            payload: dict[str, Any] = {
                "model": self.model_id,
                "messages": pruned_msgs,
                "tools": TOOL_DEFINITIONS,
                "tool_choice": "auto",
            }
            if "qwen" in self.model_id.lower():
                payload["reasoning_effort"] = self.reasoning_effort

            prov_start = time.perf_counter()
            try:
                resp = http_request("/chat/completions", method="POST", payload=payload)
            except RuntimeError as exc:
                exc_str = str(exc).lower()
                if (
                    "tokens per day" in exc_str
                    or "tpd" in exc_str
                    or "rate_limit_exceeded" in exc_str
                ):
                    curr_idx = (
                        PREFERRED_MODELS.index(self.model_id)
                        if self.model_id in PREFERRED_MODELS
                        else -1
                    )
                    if curr_idx >= 0 and curr_idx + 1 < len(PREFERRED_MODELS):
                        next_model = PREFERRED_MODELS[curr_idx + 1]
                        print(
                            f"[Model Fallback] {self.model_id} hit daily quota/limit; "
                            f"switching to {next_model}..."
                        )
                        self.model_id = next_model
                        payload["model"] = self.model_id
                        payload.pop("reasoning_effort", None)
                        resp = http_request("/chat/completions", method="POST", payload=payload)
                    else:
                        raise
                elif (
                    "tool_use_failed" in exc_str
                    or "tool call validation failed" in exc_str
                    or "output_parse_failed" in exc_str
                    or "parsing failed" in exc_str
                ):
                    print(
                        "[Tool/Output Parse Recovery] Groq grammar/parser error; "
                        "prompting agent to use a valid tool call..."
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your previous response could not be parsed as a valid tool call. "
                            "Please execute an action by calling one of the available tools "
                            "(e.g. search_code, read_file, edit_file, run_tests, finish)."
                        ),
                    })
                    continue
                else:
                    raise

            usage = resp.get("usage", {})
            provider_latency += time.perf_counter() - prov_start
            total_inp_tokens += usage.get("prompt_tokens", 0)
            total_out_tokens += usage.get("completion_tokens", 0)

            choice_obj = resp.get("choices", [{}])[0]
            message_obj = choice_obj.get("message", {})
            messages.append(message_obj)

            tool_calls = message_obj.get("tool_calls", [])
            if not tool_calls:
                content = message_obj.get("content", "")
                claims_done = "FINISH" in content or "fixed" in content.lower()
                if claims_done and edits_made > 0:
                    completed = True
                    break
                messages.append({
                    "role": "user",
                    "content": (
                        "Do not describe the fix in prose. Call edit_file to apply it, then run "
                        "the named test, then call finish()."
                    ),
                })
                continue

            for tc in tool_calls:
                tool_call_count += 1
                fn_name = tc.get("function", {}).get("name", "")
                args_str = tc.get("function", {}).get("arguments", "{}")
                try:
                    args = json.loads(args_str)
                except Exception:
                    args = {}

                tool_start = time.perf_counter()
                tool_output, agent_event = self.executor.execute(fn_name, args)
                tool_latency += time.perf_counter() - tool_start
                recorded_events.append(agent_event)
                trajectory_log.append(f"[{fn_name}] {args} -> {agent_event.event_type}")

                if fn_name == "search_code":
                    searches += 1
                elif fn_name in ("run_command", "run_tests"):
                    tests_run += 1
                    if agent_event.event_type in ("command_failed", "test_failed"):
                        failed_commands += 1
                elif fn_name == "edit_file":
                    if agent_event.event_type == "file_edit":
                        edits_made += 1
                elif fn_name == "finish":
                    completed = True

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", f"call_{tool_call_count}"),
                    "name": fn_name,
                    "content": tool_output[:800],
                })

                if adapter is not None and detector is not None:
                    adapter.record_event(agent_event)
                    detector.note(agent_event)
                    opp = detector.check(adapter.window.events(), task_id)
                    if opp is not None:
                        ml_start = time.perf_counter()
                        ml_decisions += 1
                        rec_dec = adapter.decide(task_id=opp.opportunity_id)
                        ml_latency += time.perf_counter() - ml_start
                        pending_decisions.append((rec_dec.decision_id, len(recorded_events)))
                        opportunities.append({
                            "opportunity_id": opp.opportunity_id,
                            "reason": opp.reason,
                            "signals": opp.signals,
                            "event_index": opp.event_index,
                            "choice": rec_dec.choice,
                            "source": rec_dec.source,
                        })
                        teacher_counts[rec_dec.choice] = teacher_counts.get(rec_dec.choice, 0) + 1
                        trajectory_log.append(
                            f"[Ink] opportunity={opp.reason} choice={rec_dec.choice} "
                            f"source={rec_dec.source}"
                        )
                        if rec_dec.source == "fast_path":
                            ml_serves += 1
                        else:
                            ml_fallbacks += 1

                        if rec_dec.choice == "retrieve_context" and rec_dec.context_patch:
                            ml_patch_tokens += rec_dec.context_patch.token_count
                            if observe_only:
                                ml_retrievals += 1
                                trajectory_log.append(
                                    "[Ink] observe: patch proposed, not injected"
                                )
                            else:
                                ml_retrievals += 1
                                patch_msg = (
                                    f"[Ink Recovery Context]\n"
                                    f"Question: {rec_dec.context_patch.question}\n"
                                    f"Evidence:\n"
                                )
                                for ev_item in rec_dec.context_patch.evidence[:5]:
                                    src = ev_item.get("source")
                                    pth = ev_item.get("path", "")
                                    ln = ev_item.get("line", "")
                                    cnt = ev_item.get("content", "")
                                    patch_msg += f"- [{src}] {pth}:{ln} {cnt}\n"
                                messages.append({"role": "system", "content": patch_msg})
                                toks = rec_dec.context_patch.token_count
                                trajectory_log.append(
                                    f"[Ink] ContextPatch injected: {toks} tokens"
                                )
                        elif rec_dec.choice == "replan" and not observe_only:
                            replan_msg = (
                                "[Ink Recovery Action: replan] The current modification "
                                "path has failed multiple times. Please re-read the original "
                                "error, step back, and reconsider the architectural root cause."
                            )
                            messages.append({"role": "system", "content": replan_msg})
                            trajectory_log.append("[Ink] Replan intervention injected")

            # Loop-breaker: a bounded action budget is only useful if the agent does not
            # spend it repeating the same tool call. After four identical actions, or once
            # half the budget is gone with no edit attempted, escalate the instruction.
            sig = "|".join(
                sorted(
                    "{}:{}".format(
                        tc.get("function", {}).get("name", ""),
                        tc.get("function", {}).get("arguments", ""),
                    )
                    for tc in tool_calls
                )
            )
            recent_signatures.append(sig)
            if not completed and len(recent_signatures) == 4 and len(set(recent_signatures)) == 1:
                messages.append({
                    "role": "user",
                    "content": (
                        "You are repeating the same action with no progress. Stop. Re-read the "
                        "bug report: it names the exact file and function to change. Call "
                        "read_file on that file, then call edit_file to make the fix."
                    ),
                })
                trajectory_log.append("[Harness] Repeated-action loop-breaker triggered")
                recent_signatures.clear()
            elif (
                not completed
                and not nudged_no_edit
                and edits_made == 0
                and tool_call_count >= max(6, self.max_tool_actions // 2)
            ):
                messages.append({
                    "role": "user",
                    "content": (
                        "You have inspected the repository without editing any file. The root "
                        "cause is described in the bug report. Make the source edit now, then "
                        "run the named test."
                    ),
                })
                trajectory_log.append("[Harness] No-edit nudge triggered")
                nudged_no_edit = True

            if completed:
                break

        if adapter is not None:
            for dec_id, idx in pending_decisions:
                before = recorded_events[:idx]
                after = recorded_events[idx:idx + EVAL_WINDOW]
                try:
                    adapter.record_outcome(dec_id, before, after, eval_window=EVAL_WINDOW)
                except Exception:
                    pass

        wall_clock = time.time() - started
        diff_res = subprocess.run(
            ["git", "diff"], cwd=self.worktree_dir, capture_output=True, text=True
        )
        final_diff = diff_res.stdout

        feats = extract_trajectory_features(recorded_events)
        repeated_actions = feats["repeated_search_count"] + feats["repeated_file_read_count"]

        return LiveTaskExecutionResult(
            task_id=task_id,
            condition=condition,
            completed=completed,
            model_id=self.model_id,
            model_calls=model_calls,
            input_tokens=total_inp_tokens,
            output_tokens=total_out_tokens,
            total_tokens=total_inp_tokens + total_out_tokens,
            wall_clock_sec=round(wall_clock, 2),
            tool_calls=tool_call_count,
            searches=searches,
            repeated_actions=repeated_actions,
            failed_commands=failed_commands,
            tests_run=tests_run,
            ink_decisions=ml_decisions,
            local_fast_path_serves=ml_serves,
            fallbacks=ml_fallbacks,
            context_retrievals=ml_retrievals,
            context_patch_tokens=ml_patch_tokens,
            provider_latency_sec=round(provider_latency, 2),
            tool_latency_sec=round(tool_latency, 2),
            ink_latency_sec=round(ml_latency, 3),
            opportunities=len(opportunities),
            opportunity_reasons={
                r: sum(1 for o in opportunities if o["reason"] == r)
                for r in {o["reason"] for o in opportunities}
            },
            teacher_action_counts=dict(teacher_counts),
            diff=final_diff,
            events=[asdict(e) for e in recorded_events],
            raw_trajectory_excerpt="\n".join(trajectory_log[:25]),
        )
