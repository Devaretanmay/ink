"""The coding harness: a real agent loop whose runtime Microloop controls.

``integrations.coding_harness`` is an integration, not core. It knows how to run
a task against a provider; Microloop only ever sees events and recommends a
runtime action.
"""
from __future__ import annotations

from integrations.coding_harness.harness import CodingHarness, RunResult, Task
from integrations.coding_harness.providers import (
    AgentBehaviour,
    AnthropicProvider,
    Ceiling,
    ModelReply,
    Provider,
    SimulatedCodingProvider,
    ToolCall,
)
from integrations.coding_harness.tasks import build_tasks, task_count

__all__ = [
    "AgentBehaviour",
    "AnthropicProvider",
    "Ceiling",
    "CodingHarness",
    "ModelReply",
    "Provider",
    "RunResult",
    "SimulatedCodingProvider",
    "Task",
    "ToolCall",
    "build_tasks",
    "task_count",
]
