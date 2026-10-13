"""Public exception hierarchy for Ink.

All Ink exceptions inherit from `InkError`, allowing callers to catch any
Ink-specific failure or inspect specific failure modes.
"""

from __future__ import annotations


class InkError(Exception):
    """Base exception for all Ink runtime, contract, and artifact errors."""


class ContractError(InkError, ValueError):
    """Raised when a DecisionSite definition, state schema, or choice contract is violated."""


class ArtifactError(InkError):
    """Raised when an engine artifact, model checkpoint, or representation weight is invalid or missing."""


class StorageError(InkError):
    """Raised when the SQLite evidence ledger or artifact store cannot be accessed or migrated."""


class ConfigurationError(InkError, ValueError):
    """Raised when Ink runtime configuration or promotion requirements are invalid."""
