"""Adaptive runtime layer: runtime state, actions, controller and episode.

The runtime layer answers a second question alongside progress: *under what
conditions is this run advancing, and what should change?* Pass 1 defined the
vocabulary and the decision surface, Pass 2 added the rule actuators, Pass 3
made action selection scored, and Pass 4 adds the provider-neutral integration
surface: model tiers, deterministic compaction, adaptation results and task
outcomes.
"""
from __future__ import annotations

from .action import RuntimeAction
from .adapter import (
    DEFAULT_REPLAN_MESSAGE,
    AgentRuntimeAdapter,
    ApplyResult,
    MockAdapter,
    RuntimeAdapter,
    TieredAdapter,
)
from .capabilities import Capabilities, CapabilityLevel
from .compaction import CompactionReport, ContextCompactor, Segment
from .controller import RuntimeController, ScoredController
from .decision import (
    ActionScore,
    ControllerTrace,
    ProgressSnapshot,
    RecommendationReason,
    RuntimeDecision,
    Strategy,
)
from .episode import AdaptationRecord, Episode
from .outcome import ActionOutcome
from .result import AdaptationResult, TaskOutcome
from .session import RuntimeSession
from .state import Budget, RuntimeState, Usage
from .tier import ModelTier

__all__ = [
    "ActionOutcome",
    "ActionScore",
    "AdaptationRecord",
    "AdaptationResult",
    "AgentRuntimeAdapter",
    "ApplyResult",
    "Budget",
    "Capabilities",
    "CapabilityLevel",
    "CompactionReport",
    "ContextCompactor",
    "ControllerTrace",
    "DEFAULT_REPLAN_MESSAGE",
    "Episode",
    "MockAdapter",
    "ModelTier",
    "ProgressSnapshot",
    "RecommendationReason",
    "RuntimeAction",
    "RuntimeAdapter",
    "RuntimeController",
    "RuntimeDecision",
    "RuntimeSession",
    "RuntimeState",
    "ScoredController",
    "Segment",
    "Strategy",
    "TaskOutcome",
    "TieredAdapter",
    "Usage",
]
