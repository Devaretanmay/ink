"""
Microloop + LiteLLM Guardrail Integration
=========================================

Official high-performance Microloop integration for LiteLLM.
Uses the Rust-backed Microloop engine to detect agent loops in
under a microsecond — before ever reaching the LLM.

Install: pip install "microloop[litellm]"
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

# ── Graceful optional-litellm import ──────────────────────────────────────────

try:
    from litellm.integrations.custom_guardrail import CustomGuardrail
    from litellm.exceptions import BadRequestError

    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False
    CustomGuardrail = object  # type: ignore[misc,assignment]
    BadRequestError = None  # type: ignore[assignment,misc]

    logging.getLogger(__name__).warning(
        "LiteLLM is not installed. "
        "Install it with: pip install 'microloop[litellm]'"
    )

# ── Core engine import (always available — microloop is a core dep) ────────────

from microloop.microloop_core import Microloop  # noqa: E402

logger = logging.getLogger(__name__)


# ── Exception ─────────────────────────────────────────────────────────────────

class MicroloopLoopDetected(ValueError):
    """Raised when Microloop detects a repeating tool call trajectory."""

    def __init__(
        self,
        tool_name: str,
        repeat_count: int,
        max_repeats: int,
        session_id: str = "",
    ) -> None:
        self.tool_name = tool_name
        self.repeat_count = repeat_count
        self.max_repeats = max_repeats
        self.session_id = session_id
        msg = (
            f"Microloop: Loop detected on tool '{tool_name}' — "
            f"seen {repeat_count}× (limit: {max_repeats})"
            + (f" in session '{session_id}'" if session_id else "")
        )
        super().__init__(msg)


# ── Guardrail ─────────────────────────────────────────────────────────────────

class MicroloopLiteLLMGuardrail(CustomGuardrail):
    """
    Official Microloop LiteLLM guardrail.

    Hooks into LiteLLM's ``async_pre_call_hook`` to examine every tool call
    before it reaches the LLM.  If the same tool + arguments repeat beyond
    ``max_repeats`` within a sliding window, the call is blocked and a
    ``BadRequestError`` is raised — saving API tokens and latency.

    Parameters
    ----------
    max_repeats:
        Number of identical calls before blocking. Default ``3``.
    history_window:
        Sliding-window size (number of past calls examined). Default ``10``.
    volatile_fields:
        JSON field names excluded from comparison (e.g. ``['req_id']``).
        Microloop auto-infers high-entropy fields if left empty.
    **kwargs:
        Forwarded to :class:`CustomGuardrail`.
    """

    def __init__(
        self,
        max_repeats: int = 3,
        history_window: int = 10,
        volatile_fields: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> None:
        if not LITELLM_AVAILABLE:
            raise ImportError(
                "LiteLLM is not installed. "
                "Install it with: pip install 'microloop[litellm]'"
            )

        # LiteLLM base initialisation
        super().__init__(
            guardrail_name="microloop",
            supported_event_hooks=["pre_call"],
            default_on=True,
            **kwargs,
        )

        self._max_repeats = max_repeats
        self._history_window = history_window
        self._volatile_fields = list(volatile_fields or [])

        # Per-session Microloop engines: session_id → Microloop instance
        self._engines: Dict[str, Microloop] = {}

        logger.info(
            "Microloop LiteLLM guardrail initialised "
            "(max_repeats=%d, window=%d)",
            max_repeats,
            history_window,
        )

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _get_engine(self, session_id: str) -> Microloop:
        """Return (or lazily create) a Microloop engine for *session_id*."""
        if session_id not in self._engines:
            cfg = json.dumps(
                {
                    "max_repeats": self._max_repeats,
                    "history_window": self._history_window,
                    "volatile_fields": self._volatile_fields,
                }
            )
            self._engines[session_id] = Microloop(cfg)
        return self._engines[session_id]

    @staticmethod
    def _extract_tool_call(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Pull the most-recent tool call dict from a LiteLLM request body."""
        messages = data.get("messages") or []
        if not messages:
            return None
        last = messages[-1]
        # LiteLLM normalizes all provider formats to OpenAI-style tool_calls
        for call in last.get("tool_calls") or []:
            fn = call.get("function") or {}
            if fn.get("name"):
                return fn
        return None

    @staticmethod
    def _get_session_id(data: Dict[str, Any]) -> str:
        metadata = data.get("metadata") or {}
        return str(
            metadata.get("session_id") or data.get("litellm_session_id", "default")
        )

    # ── LiteLLM hook ───────────────────────────────────────────────────────────

    async def async_pre_call_hook(
        self,
        user_model_dict: Dict[str, Any],
        cache: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Intercept the request before it reaches the model.

        Returns ``None`` to allow the call, or a modified dict to override it.
        Raises :class:`MicroloopLoopDetected` to block it entirely.
        """
        tool_call = self._extract_tool_call(user_model_dict)
        if tool_call is None:
            return None  # Not a tool call — allow

        session_id = self._get_session_id(user_model_dict)
        engine = self._get_engine(session_id)

        tool_name = tool_call.get("name", "unknown")
        raw_args = tool_call.get("arguments", "{}")
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except json.JSONDecodeError:
                pass
        args_str = raw_args if isinstance(raw_args, str) else json.dumps(raw_args)

        verdict = engine.verify(tool_name, args_str)

        if verdict != 0:
            raise MicroloopLoopDetected(
                tool_name=tool_name,
                repeat_count=self._max_repeats,
                max_repeats=self._max_repeats,
                session_id=session_id,
            )

        return None  # allow
