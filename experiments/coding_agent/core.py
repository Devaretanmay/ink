"""Decision-site normalization, verification, and Ink lifecycle integration."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from ink import DecisionSite, FallbackResult, Ink

Mode = Literal["control", "observe", "shadow", "active"]
LABELS = ("success", "code_failure", "dependency", "environment", "unknown")

CLASSIFICATION_SITE = DecisionSite(
    name="tool_result.classify",
    state_schema={
        "tool_name": "string",
        "command_family": "string",
        "exit_code": "integer?",
        "timed_out": "boolean",
        "stdout_summary": "string",
        "stderr_summary": "string",
        "retry_count": "integer",
    },
    choices=LABELS,
    fallback_revision="deterministic-tool-result-v1",
    fallback_model_calls=0,
)

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
)
_TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+Z?\b")
_TMP_RE = re.compile(r"(?:/private)?/tmp/[\w./-]+|/var/folders/[\w./-]+")
_PATH_RE = re.compile(r"/(?:Users|home)/[^\s:'\"]+(?:/[^\s:'\"]+)*")
_PORT_RE = re.compile(r"(?<=:)(?:[1-9]\d{3,4})\b")
_ERROR_MARKERS = (
    "error",
    "failed",
    "failure",
    "exception",
    "traceback",
    "not found",
    "denied",
    "timeout",
)

_ENV_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"command not found",
        r"no such file or directory.*(?:python|node|npm|cargo|go|java|gcc|clang)",
        r"permission denied",
        r"read-only file system",
        r"no space left on device",
        r"disk quota exceeded",
        r"cannot allocate memory|out of memory|killed: 9",
        r"unsupported (?:platform|architecture)|exec format error",
        r"docker daemon|container.*(?:failed|unavailable)",
        r"connection refused|service unavailable",
    )
)
_DEPENDENCY_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"ModuleNotFoundError|ImportError:.*cannot import",
        r"cannot find module|module not found",
        r"no matching distribution found|could not find a version that satisfies",
        r"failed to resolve|unable to resolve dependency|dependency conflict",
        r"package .* is not installed|missing dependency",
        r"failed to fetch|could not download|registry.*(?:unavailable|timed out)",
        r"lock file.*(?:out of date|incompatible)",
    )
)
_CODE_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"AssertionError|assertion failed",
        r"SyntaxError|TypeError|NameError|AttributeError|ValueError",
        r"(?:FAILED|FAILURES?)\s+(?:tests?/|[\w.-]+::)",
        r"\b(?:pytest|jest|vitest|go test|cargo test)\b.*\bfailed\b",
        r"(?:compile|build|lint|typecheck)(?:r|ing)?(?:\s+error|.*failed)",
        r"error\[[A-Z]\d+\]|TS\d{4}:",
        r"expected .* (?:but|got|to equal)",
    )
)


@dataclass(frozen=True)
class ToolObservation:
    tool_name: str
    command: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    retry_count: int = 0
    call_id: str | None = None
    started_at: float | None = None
    ended_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Verification:
    label: str
    evidence: dict[str, Any]


def _command_family(command: str, tool_name: str) -> str:
    words = command.strip().split()
    executable = Path(words[0]).name.lower() if words else tool_name.lower()
    joined = " ".join(words[:3]).lower()
    if "pytest" in joined or executable in {"pytest", "py.test"}:
        return "pytest"
    if executable in {"npm", "pnpm", "yarn", "bun"} and any(
        word in words[:3] for word in ("test", "run")
    ):
        return "javascript_test"
    if joined.startswith("go test"):
        return "go_test"
    if joined.startswith("cargo test"):
        return "rust_test"
    if executable in {"pip", "pip3", "uv", "npm", "pnpm", "yarn", "cargo", "go"} and any(
        word in words[:3] for word in ("install", "add", "get", "sync")
    ):
        return "package_install"
    if executable in {"gcc", "g++", "clang", "clang++", "rustc", "tsc", "mypy"}:
        return "compiler"
    if executable in {"ruff", "eslint", "pylint", "golangci-lint"}:
        return "lint"
    if tool_name.lower() in {"bash", "shell", "terminal", "exec", "command"}:
        return "shell"
    return tool_name.lower() or "unknown"


def _normalize_text(value: str, *, limit: int = 2000) -> str:
    text = _ANSI_RE.sub("", value or "")
    text = _UUID_RE.sub("<uuid>", text)
    text = _TIMESTAMP_RE.sub("<timestamp>", text)
    text = _TMP_RE.sub("<tmp>", text)
    text = _PATH_RE.sub("<path>", text)
    text = _PORT_RE.sub("<port>", text)
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    error_line = next(
        (line for line in lines if any(marker in line.lower() for marker in _ERROR_MARKERS)),
        None,
    )
    selected = ([error_line] if error_line else []) + lines[-20:]
    deduplicated = list(dict.fromkeys(selected))
    result = "\n".join(deduplicated)
    if len(result) > limit:
        result = result[: limit - 14].rstrip() + "\n<truncated>"
    return result


def normalize_state(observation: ToolObservation) -> dict[str, Any]:
    """Return the smallest stable flat state accepted by DecisionSite."""
    return {
        "tool_name": observation.tool_name.lower() or "unknown",
        "command_family": _command_family(observation.command, observation.tool_name),
        "exit_code": observation.exit_code,
        "timed_out": observation.timed_out,
        "stdout_summary": _normalize_text(observation.stdout),
        "stderr_summary": _normalize_text(observation.stderr),
        "retry_count": max(0, observation.retry_count),
    }


def _matches(patterns: tuple[re.Pattern[str], ...], text: str) -> list[str]:
    return [pattern.pattern for pattern in patterns if pattern.search(text)]


def verify_tool_result(observation: ToolObservation) -> Verification:
    """Map raw process evidence to one label without using a model."""
    text = "\n".join((observation.stderr, observation.stdout))
    environment = _matches(tuple(_ENV_PATTERNS), text)
    dependency = _matches(tuple(_DEPENDENCY_PATTERNS), text)
    code = _matches(tuple(_CODE_PATTERNS), text)
    signals = [name for name, matches in (("environment", environment), ("dependency", dependency), ("code_failure", code)) if matches]

    if observation.timed_out and not signals:
        label = "unknown"
        reason = "unattributed_timeout"
    elif len(signals) > 1:
        label = "unknown"
        reason = "conflicting_signals"
    elif signals:
        label = signals[0]
        reason = "deterministic_pattern"
    elif observation.exit_code == 0:
        label = "success"
        reason = "zero_exit"
    else:
        label = "unknown"
        reason = "unrecognized_nonzero_exit" if observation.exit_code is not None else "no_exit_status"

    return Verification(
        label,
        {
            "verifier": "tool_result_evidence_v1",
            "reason": reason,
            "exit_code": observation.exit_code,
            "timed_out": observation.timed_out,
            "environment_patterns": environment,
            "dependency_patterns": dependency,
            "code_patterns": code,
            "call_id": observation.call_id,
            "raw_stdout_tail": observation.stdout[-4000:],
            "raw_stderr_tail": observation.stderr[-4000:],
            "metadata": observation.metadata,
        },
    )


class InkInstrumentor:
    """Apply an Ink mode to already-recorded agent events.

    Instrumentation is deliberately post-run. It therefore cannot change the
    coding agent's patch while this DecisionSite remains observational.
    """

    def __init__(self, mode: Mode, db_path: Path | str):
        if mode not in ("control", "observe", "shadow", "active"):
            raise ValueError(f"Unsupported mode: {mode}")
        self.mode = mode
        self.db_path = Path(db_path)
        self.client: Ink | None = None
        if mode != "control":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.client = Ink(
                self.db_path,
                auto_maintenance=False,
                disable_fast_path=mode == "observe",
            )
            self.client.register(CLASSIFICATION_SITE)
            status = self.client.inspect(CLASSIFICATION_SITE)["state"]
            if mode == "shadow" and status == "ACTIVE":
                raise RuntimeError("shadow mode refuses an ACTIVE artifact; use a shadow database")

    def close(self) -> None:
        if self.client is not None:
            self.client.close()

    def __enter__(self) -> InkInstrumentor:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def classify(self, observation: ToolObservation, task_id: str) -> dict[str, Any] | None:
        if self.mode == "control":
            return None
        assert self.client is not None
        state = normalize_state(observation)
        verification = verify_tool_result(observation)
        started = time.perf_counter()
        result = self.client.decide(
            site=CLASSIFICATION_SITE,
            state=state,
            task_id=task_id,
            fallback=lambda: FallbackResult(verification.label, model_calls=0),
        )
        latency_ms = (time.perf_counter() - started) * 1000

        history = self.client.store.history(CLASSIFICATION_SITE.version)
        row = next((item for item in reversed(history) if item["id"] == result.decision_id), None)
        prediction = row.get("prediction") if row else None
        predicted_choice = prediction.get("choice") if prediction else None
        if self.mode in ("observe", "shadow") and result.source != "fallback":
            raise RuntimeError(f"{self.mode} mode must never serve locally")

        candidate_choice = predicted_choice if predicted_choice is not None else (
            result.choice if result.source == "fast_path" else None
        )
        candidate_correct = (
            candidate_choice == verification.label if candidate_choice is not None else None
        )
        served_correct = result.choice == verification.label if result.source == "fast_path" else None
        self.client.record_outcome(
            result.decision_id,
            quality=1.0 if result.choice == verification.label else 0.0,
            verifier="tool_result_evidence_v1",
            verifier_version="1",
            evidence=verification.evidence,
        )
        engine = None
        if result.fast_path_version:
            artifact = self.client.store.rows(
                "SELECT payload FROM artifacts WHERE id=?", (result.fast_path_version,)
            )
            if artifact:
                engine = json.loads(artifact[0]["payload"])["engine_data"].get("engine")

        return {
            "record_type": "decision",
            "task_id": task_id,
            "site": CLASSIFICATION_SITE.name,
            "state": state,
            "fallback_choice": verification.label,
            "decision_id": result.decision_id,
            "source": result.source,
            "fallback_reason": result.fallback_reason,
            "confidence": result.confidence,
            "latency_ms": latency_ms,
            "predicted_choice": predicted_choice,
            "candidate_correct": candidate_correct,
            "served_correct": served_correct,
            "engine": engine,
            "comparison": result.fallback_reason == "comparison",
            "verification": asdict(verification),
        }
