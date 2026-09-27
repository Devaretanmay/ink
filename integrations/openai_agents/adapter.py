"""The OpenAI Agents SDK adapter.

It maps the provider-neutral Microloop tiers onto concrete OpenAI model ids and
performs the three real adaptations:

* ``replan``    -- queues a short message for the host to inject;
* ``escalate_model`` / ``deescalate_model`` -- moves the tier; the host reads
  ``adapter.model`` for the next turn;
* ``compact_context`` -- runs the deterministic compactor over the recorded
  segments.

Microloop core never learns a model name: the map lives here.
"""
from __future__ import annotations

from collections.abc import Mapping

from microloop import (
    Capabilities,
    ContextCompactor,
    ModelTier,
    TieredAdapter,
)

__all__ = ["DEFAULT_OPENAI_TIERS", "OpenAIAgentsAdapter"]

#: Default tier map, weakest to strongest. Override to match whatever model ids
#: your account can use; nothing downstream depends on these names.
DEFAULT_OPENAI_TIERS: dict[str, str] = {
    ModelTier.Fast: "gpt-4o-mini",
    ModelTier.Balanced: "gpt-4o",
    ModelTier.Strong: "o3",
}


class OpenAIAgentsAdapter(TieredAdapter):
    """Tiered adapter for OpenAI Agents SDK runs."""

    def __init__(
        self,
        tiers: Mapping[str, str] | None = None,
        *,
        start: str | None = None,
        context_limit: int | None = 128_000,
        capabilities: Capabilities | None = None,
    ) -> None:
        super().__init__(
            tiers or DEFAULT_OPENAI_TIERS,
            start=start,
            context_limit=context_limit,
            capabilities=capabilities
            or Capabilities(replan=True, model_switch=True, context_compaction=True),
            compactor=ContextCompactor(),
        )
