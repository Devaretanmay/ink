"""Deterministic context compaction.

This is deliberately *not* an AI summarizer. It is a fixed rule set over labelled
transcript segments: keep what a resuming agent needs to keep making progress,
drop what is verbose, superseded or duplicated. Being deterministic means the
same transcript always compacts to the same result, so a run can be replayed.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["CompactionReport", "ContextCompactor", "EssentialKinds", "Segment"]


class EssentialKinds:
    """Segment kinds that survive compaction unconditionally."""

    #: The task, the current plan, decisions, the latest errors, verification
    #: state and the files changed. These are what an agent needs to resume.
    All = frozenset({"task", "plan", "decision", "error", "verification", "files"})

    #: Verbatim chatter that is dropped first when the transcript is too long.
    Disposable = frozenset({"tool", "exploration", "note"})


@dataclass
class Segment:
    """One labelled piece of a transcript."""

    kind: str
    text: str
    step: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Segment:
        return cls(
            kind=str(payload.get("kind", "note")),
            text=str(payload.get("text", "")),
            step=None if payload.get("step") is None else int(payload["step"]),
            meta=dict(payload.get("meta") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind, "text": self.text}
        if self.step is not None:
            payload["step"] = self.step
        if self.meta:
            payload["meta"] = dict(self.meta)
        return payload


@dataclass
class CompactionReport:
    """What a compaction kept and dropped, for the episode record."""

    kept: list[Segment] = field(default_factory=list)
    dropped: list[Segment] = field(default_factory=list)

    @property
    def kept_count(self) -> int:
        return len(self.kept)

    @property
    def dropped_count(self) -> int:
        return len(self.dropped)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kept": self.kept_count,
            "dropped": self.dropped_count,
            "dropped_kinds": sorted({segment.kind for segment in self.dropped}),
        }


def _normalize(text: str) -> str:
    return " ".join(text.split()).lower()


class ContextCompactor:
    """Compacts a transcript by fixed rules.

    Order of operations, all deterministic:

    1. Keep every segment of an essential kind (task, plan, decision, error,
       verification, files) in original order.
    2. Of the disposable kinds, keep only the most recent ``keep_recent``.
    3. Drop an earlier segment when a later one has identical normalized text,
       so a superseded plan or a repeated error is not stored twice.
    4. If the result still exceeds ``max_segments``, drop the oldest disposable
       segments first.
    """

    def __init__(self, *, keep_recent: int = 8, max_segments: int = 24) -> None:
        if keep_recent < 0:
            raise ValueError("keep_recent must be >= 0")
        if max_segments < 1:
            raise ValueError("max_segments must be >= 1")
        self.keep_recent = keep_recent
        self.max_segments = max_segments

    def compact(self, segments: Iterable[Segment | Mapping[str, Any]]) -> CompactionReport:
        items = [
            segment if isinstance(segment, Segment) else Segment.from_dict(segment)
            for segment in segments
        ]
        kept: list[Segment] = []
        dropped: list[Segment] = []

        # 1 + 2: split essential from disposable.
        essential = [segment for segment in items if segment.kind in EssentialKinds.All]
        disposable = [
            segment for segment in items if segment.kind not in EssentialKinds.All
        ]
        start = max(0, len(disposable) - self.keep_recent) if self.keep_recent else len(disposable)
        recent = set(range(start, len(disposable)))
        disposable_kept = [
            segment for index, segment in enumerate(disposable) if index in recent
        ]

        dropped.extend(
            segment for index, segment in enumerate(disposable) if index not in recent
        )

        # 3: deduplicate by normalized text, keeping the latest occurrence.
        ordered = essential + disposable_kept
        latest: dict[str, int] = {}
        for index, segment in enumerate(ordered):
            key = _normalize(segment.text)
            if key:
                latest[key] = index
        for index, segment in enumerate(ordered):
            key = _normalize(segment.text)
            if key and latest[key] != index:
                dropped.append(segment)
                continue
            kept.append(segment)

        # 4: enforce the hard cap, dropping the oldest disposable first.
        if len(kept) > self.max_segments:
            overflow = len(kept) - self.max_segments
            survivors: list[Segment] = []
            for segment in kept:
                if overflow > 0 and segment.kind not in EssentialKinds.All:
                    dropped.append(segment)
                    overflow -= 1
                else:
                    survivors.append(segment)
            # If everything left is essential, keep it: correctness beats the cap.
            kept = survivors

        kept.sort(key=lambda segment: (segment.step is None, segment.step or 0))
        return CompactionReport(kept=kept, dropped=dropped)
