"""Progressive evidence epochs and region lifecycle management."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from ..errors import StorageError
from ..internal.decision_store import DecisionStore

logger = logging.getLogger("ink.runtime.epoch_manager")


class EpochManager:
    """Coordinates durable SQLite evidence epochs and region serving lifecycles."""

    def __init__(self, store: DecisionStore, app_id: str = "default") -> None:
        self.store = store
        self.app_id = app_id

    def open_or_get_epoch(
        self,
        site: str,
        site_version: str,
        artifact_id: str,
        representation_version: str,
        engine: str,
        verifier: str,
        verifier_version: str,
        schema_hash: str,
        choices_hash: str,
        requirements: dict[str, Any] | str,
    ) -> str:
        """Opens or retrieves an existing compatible open evidence epoch.

        Raises StorageError on database failures rather than silently swallowing.
        """
        try:
            return self.store.open_or_get_epoch(
                app_id=self.app_id,
                site=site,
                site_version=site_version,
                artifact_id=artifact_id,
                representation_version=representation_version,
                engine=engine,
                verifier=verifier,
                verifier_version=verifier_version,
                schema_hash=schema_hash,
                choices_hash=choices_hash,
                requirements=requirements,
            )
        except sqlite3.Error as exc:
            logger.error("Failed to open or get evidence epoch for site %s: %s", site, exc)
            raise StorageError(f"Database error managing evidence epoch: {exc}") from exc

    def sync_region_lifecycle(
        self,
        artifact_id: str,
        site_version: str,
        region_id: str,
        status: str,
        radius: float,
        negative_margin: float,
        sample_count: int = 0,
        verified_positive: int = 0,
        verified_negative: int = 0,
        quality_lower: float = 0.0,
    ) -> None:
        """Persists or updates an individual coverage/semantic region lifecycle state."""
        try:
            self.store.sync_region_lifecycle(
                artifact_id=artifact_id,
                site=site_version,
                region_id=region_id,
                status=status,
                radius=radius,
                negative_margin=negative_margin,
                sample_count=sample_count,
                verified_positive=verified_positive,
                verified_negative=verified_negative,
                quality_lower=quality_lower,
            )
        except sqlite3.Error as exc:
            logger.error("Failed to sync region lifecycle for region %s: %s", region_id, exc)
            raise StorageError(f"Database error syncing region lifecycle: {exc}") from exc

    def record_progressive_evidence(
        self,
        epoch_id: str,
        region_id: str,
        decision_id: str,
        state_canonical: str,
        choice: str,
        expected_choice: str | None,
        quality: float,
        verified_match: int,
        distance: float | None = None,
    ) -> None:
        """Appends verified outcome evidence to an open evidence epoch."""
        try:
            self.store.record_progressive_evidence(
                epoch_id=epoch_id,
                region_id=region_id,
                decision_id=decision_id,
                state_canonical=state_canonical,
                choice=choice,
                expected_choice=expected_choice,
                quality=quality,
                verified_match=verified_match,
                distance=distance,
            )
        except sqlite3.Error as exc:
            logger.error("Failed to record progressive evidence for epoch %s: %s", epoch_id, exc)
            raise StorageError(f"Database error recording progressive evidence: {exc}") from exc

    def get_progressive_evidence(self, epoch_id: str, region_id: str | None = None) -> list[dict[str, Any]]:
        """Retrieves accumulated progressive evidence records for an epoch."""
        return self.store.get_progressive_evidence(epoch_id, region_id=region_id)

    def get_active_regions(self, artifact_id: str) -> set[str]:
        """Returns the set of active region IDs for a qualified artifact."""
        return self.store.get_active_regions(artifact_id)

    def get_region_lifecycles(self, artifact_id: str) -> list[dict[str, Any]]:
        """Returns detailed lifecycle rows for all regions of an artifact."""
        return self.store.get_region_lifecycles(artifact_id)

    def revoke_epoch_and_regions(self, artifact_id: str, db: sqlite3.Connection | None = None) -> None:
        """Revokes open epochs and active regions upon deoptimization."""
        self.store.revoke_epoch_and_regions(artifact_id, db=db)
