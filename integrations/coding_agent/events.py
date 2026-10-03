from __future__ import annotations

import collections
import time
from dataclasses import dataclass, field
from typing import Any

VALID_EVENT_TYPES = frozenset({
    "file_read",
    "file_search",
    "file_edit",
    "edit_reverted",
    "command_run",
    "command_failed",
    "test_run",
    "test_failed",
    "test_passed",
    "tool_call",
    "tool_error",
})


@dataclass(frozen=True, slots=True)
class AgentEvent:
    event_type: str
    timestamp: float = field(default_factory=time.time)
    path: str | None = None
    command: str | None = None
    query: str | None = None
    tool_name: str | None = None
    error: str | None = None
    test_counts: dict[str, int] | None = None
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.event_type not in VALID_EVENT_TYPES:
            raise ValueError(f"Unknown agent event_type: {self.event_type!r}")


class TrajectoryWindow:
    def __init__(self, max_size: int = 30) -> None:
        if max_size < 5:
            raise ValueError("max_size must be at least 5")
        self.max_size = max_size
        self._events: collections.deque[AgentEvent] = collections.deque(maxlen=max_size)

    def append(self, event: AgentEvent) -> None:
        self._events.append(event)

    def events(self) -> list[AgentEvent]:
        return list(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def clear(self) -> None:
        self._events.clear()
