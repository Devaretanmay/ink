"""Tests for Ink Phase 19: Canonical Policy Model Rename, Ownership Migration & Backend Architecture."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import pytest

from ink import Ink
from ink.internal.model.constants import (
    INK_DECISION_SMALL,
    INK_DECISION_LARGE,
    LEGACY_INK_DECISION_V1,
    LEGACY_DECISION_V1,
    MODEL_RUNTIME_VERSION,
    resolve_policy_model_id,
    reset_deprecation_warnings,
)
from ink.internal.model.policy_registry import (
    get_model_spec,
    POLICY_MODEL_REGISTRY,
    ModelSpec,
)
from ink.internal.model import registry
from ink.internal.engines import (
    PolicyModelEngine,
    DecisionModelEngine,
    MlxDecisionEngine,
    InkDecisionSmallBackend,
    InkDecisionLargeBackend,
    PolicyProposal,
)
from ink.runtime.compiler import compute_evidence_identity
from ink.runtime.diagnostics import Diagnostics


def test_constants_and_runtime_version_decoupling():
    assert INK_DECISION_SMALL == "ink-decision-small"
    assert INK_DECISION_LARGE == "ink-decision-large"
    assert LEGACY_INK_DECISION_V1 == "ink-decision-v1"
    assert LEGACY_DECISION_V1 == "decision-v1"
    assert MODEL_RUNTIME_VERSION == "2"


def test_resolve_policy_model_id():
    assert resolve_policy_model_id("small") == INK_DECISION_SMALL
    assert resolve_policy_model_id("ink-decision-small") == INK_DECISION_SMALL
    assert resolve_policy_model_id("large") == INK_DECISION_LARGE
    assert resolve_policy_model_id("ink-decision-large") == INK_DECISION_LARGE
    assert resolve_policy_model_id("auto") == "auto"
    assert resolve_policy_model_id("disabled") is None
    assert resolve_policy_model_id("none") is None
    assert resolve_policy_model_id(None) is None

    reset_deprecation_warnings()
    with pytest.deprecated_call(match="ink-decision-v1.*deprecated"):
        resolved = resolve_policy_model_id("ink-decision-v1")
        assert resolved == INK_DECISION_SMALL

    # Second call should not warn again (warns once per session)
    resolved2 = resolve_policy_model_id("ink-decision-v1")
    assert resolved2 == INK_DECISION_SMALL

    reset_deprecation_warnings()
    with pytest.deprecated_call(match="decision-v1.*deprecated"):
        resolved_legacy = resolve_policy_model_id("decision-v1")
        assert resolved_legacy == INK_DECISION_SMALL

    with pytest.raises(ValueError, match="Unknown policy model"):
        resolve_policy_model_id("non-existent-model")


def test_policy_model_registry_specs():
    small_spec = get_model_spec(INK_DECISION_SMALL)
    assert isinstance(small_spec, ModelSpec)
    assert small_spec.canonical_id == INK_DECISION_SMALL
    assert small_spec.size_tier == "small"
    assert small_spec.family == "ink-decision"
    assert small_spec.backend == "mlx"
    assert small_spec.parameter_count == 421293830
    assert LEGACY_INK_DECISION_V1 in small_spec.aliases
    assert small_spec.is_default is True

    large_spec = get_model_spec(INK_DECISION_LARGE)
    assert isinstance(large_spec, ModelSpec)
    assert large_spec.canonical_id == INK_DECISION_LARGE
    assert large_spec.size_tier == "large"
    assert large_spec.family == "ink-decision"
    assert large_spec.backend == "gliner_torch"
    assert large_spec.parameter_count == 486444053
    assert large_spec.is_default is False

    # Resolution via alias
    assert get_model_spec(LEGACY_INK_DECISION_V1).canonical_id == INK_DECISION_SMALL

    with pytest.raises(KeyError):
        get_model_spec("unknown-spec")


def test_engine_aliases():
    assert DecisionModelEngine is PolicyModelEngine
    assert MlxDecisionEngine is PolicyModelEngine
    assert PolicyModelEngine.name == "decision"


from ink.internal.contracts import DecisionSite


def test_evidence_identity_incorporates_model_and_revision():
    site = DecisionSite(
        name="test_site",
        state_schema={"task": "string"},
        choices=("a", "b"),
    )
    payload_small_rev1 = {"engine_data": {"engine": "decision", "policy_model_id": "ink-decision-small", "checkpoint_revision": "rev1"}}
    payload_small_rev2 = {"engine_data": {"engine": "decision", "policy_model_id": "ink-decision-small", "checkpoint_revision": "rev2"}}
    payload_large_rev1 = {"engine_data": {"engine": "decision", "policy_model_id": "ink-decision-large", "checkpoint_revision": "rev1"}}

    id_small_rev1, _ = compute_evidence_identity(site, payload_small_rev1)
    id_small_rev2, _ = compute_evidence_identity(site, payload_small_rev2)
    id_large_rev1, _ = compute_evidence_identity(site, payload_large_rev1)

    assert id_small_rev1 != id_small_rev2
    assert id_small_rev1 != id_large_rev1


def test_diagnostics_reports_canonical_metadata():
    site = DecisionSite(
        name="test_diag_site",
        state_schema={"task": "string"},
        choices=("a", "b"),
    )
    with Ink(":memory:", policy_model="small") as loop:
        for i in range(30):
            res = loop.decide(site=site, state={"task": "a" if i % 2 == 0 else "b"}, task_id=f"t-{i}", fallback=lambda: "a" if i % 2 == 0 else "b")
            loop.record_outcome(res.decision_id, quality=1.0, verifier="test", verifier_version="1")
        loop.compile(site)
        diag = loop.inspect(site)
        assert diag["policy_model_id"] == INK_DECISION_SMALL
        assert diag["policy_model_version"] == "1.0.0"
        assert diag["checkpoint_revision"] == "phase18-full-kd"
        assert diag["policy_backend"] == "mlx"

    with Ink(":memory:", policy_model=None) as loop:
        for i in range(30):
            res = loop.decide(site=site, state={"task": "a" if i % 2 == 0 else "b"}, task_id=f"t2-{i}", fallback=lambda: "a" if i % 2 == 0 else "b")
            loop.record_outcome(res.decision_id, quality=1.0, verifier="test", verifier_version="1")
        loop.compile(site, engine="exact")
        diag = loop.inspect(site)
        assert diag["policy_model_id"] is None
        assert diag["policy_backend"] is None


def test_client_environment_variable_selection(monkeypatch):
    monkeypatch.setenv("INK_POLICY_MODEL", "large")
    with Ink(":memory:") as loop:
        assert loop.policy_model == INK_DECISION_LARGE

    monkeypatch.setenv("INK_POLICY_MODEL", "disabled")
    with Ink(":memory:") as loop:
        assert loop.policy_model is None
        assert loop._model_enabled is False


def test_small_backend_proposal_contract():
    model_dir = Path("~/.cache/ink/models/ink-decision-small").expanduser()
    if not (model_dir / "model.safetensors").is_file():
        pytest.skip("Managed ink-decision-small checkpoint unavailable")

    backend = InkDecisionSmallBackend(checkpoint=str(model_dir))
    try:
        proposal = backend.propose({"task": "billing"}, ["invoice", "refund"])
        assert isinstance(proposal, PolicyProposal)
        assert proposal.choice in ["invoice", "refund"]
        assert isinstance(proposal.scores, dict)
        assert len(proposal.scores) == 2
        assert 0.0 <= proposal.confidence <= 1.0
        assert 0.0 <= proposal.ambiguity <= 1.0
        assert proposal.familiarity is None
        assert backend.model_id == INK_DECISION_SMALL
        assert backend.backend_name == "mlx"
    finally:
        backend.close()


def test_large_backend_lazy_loading_and_contract():
    model_dir = Path("~/.cache/ink/models/ink-decision-large").expanduser()
    if not (model_dir / "model.safetensors").is_file():
        pytest.skip("ink-decision-large checkpoint unavailable")

    backend = InkDecisionLargeBackend(checkpoint=str(model_dir))
    assert backend._extractor is None  # Lazy loading: not loaded on instantiation
    try:
        proposal = backend.propose({"text": "The refund request was denied yesterday."}, ["refund", "sales"])
        assert isinstance(proposal, PolicyProposal)
        assert proposal.choice in ["refund", "sales"]
        assert isinstance(proposal.scores, dict)
        assert len(proposal.scores) == 2
        assert 0.0 <= proposal.confidence <= 1.0
        assert 0.0 <= proposal.ambiguity <= 1.0
        # Familiarity must NOT be faked for GLiNER2
        assert proposal.familiarity is None
        assert backend.model_id == INK_DECISION_LARGE
        assert backend.backend_name == "gliner_torch"
    finally:
        backend.close()


def test_idempotent_cache_migration_with_hardlinks(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("INK_MODEL_DIR", raising=False)
    monkeypatch.delenv("INK_SMALL_MODEL_DIR", raising=False)

    legacy_dir = tmp_path / ".cache/ink/models/decision-v1"
    legacy_dir.mkdir(parents=True)
    weights = b"deterministic_weight_payload_421mb_mock"
    (legacy_dir / "model.safetensors").write_bytes(weights)
    (legacy_dir / "encoder").mkdir(parents=True)
    (legacy_dir / "encoder/config.json").write_bytes(b"{}")
    (legacy_dir / "rl_agent_config.json").write_bytes(b"{}")

    # Mock specification for verification
    monkeypatch.setattr(
        registry,
        "specification",
        lambda root=None, model_id=INK_DECISION_SMALL: {
            "name": INK_DECISION_SMALL,
            "sha256": {
                "model.safetensors": hashlib.sha256(weights).hexdigest(),
                "encoder/config.json": hashlib.sha256(b"{}").hexdigest(),
                "rl_agent_config.json": hashlib.sha256(b"{}").hexdigest(),
            },
        },
    )

    canonical_dir = tmp_path / ".cache/ink/models/ink-decision-small"
    assert not canonical_dir.exists()

    # First migration
    target = registry.ensure_installed(model_id=INK_DECISION_SMALL)
    assert target == canonical_dir
    assert canonical_dir.exists()
    assert (canonical_dir / "model.safetensors").read_bytes() == weights

    # Test that hardlink preserved inode if filesystem supports it
    src_stat = (legacy_dir / "model.safetensors").stat()
    dst_stat = (canonical_dir / "model.safetensors").stat()
    assert src_stat.st_ino == dst_stat.st_ino

    # Second migration call must be idempotent
    target2 = registry.ensure_installed(model_id=INK_DECISION_SMALL)
    assert target2 == canonical_dir


def test_zero_cosmetic_noise_assertion():
    """Verify bit-for-bit cryptographic integrity of canonical ink-decision-small."""
    small_weights = registry.model_path(INK_DECISION_SMALL) / "model.safetensors"
    if not small_weights.is_file():
        pytest.skip("Canonical small checkpoint not present for bitwise check")

    h = hashlib.sha256(small_weights.read_bytes()).hexdigest()
    assert h == "28e7e65bfbd272c6392c52616bbb409a7a425db604df442e2682ab0155593276"


def test_evidence_incompatibility_across_checkpoint_revisions():
    """Verify that evidence from S2 does NOT match evidence identity for Full KD."""
    from ink.runtime.compiler import compute_evidence_identity

    site = DecisionSite(name="compat_test", state_schema={}, choices=("x", "y"))
    s2_payload = {
        "engine_data": {
            "engine": "linear",
            "policy_model_id": INK_DECISION_SMALL,
            "checkpoint_revision": "phase18-distill-s2",
            "manifest_sha256": "b0e7e2519feb3fe8c0032b5436766ec51c4b0560654a7969aa68cd600a8fe895",
        }
    }
    full_payload = {
        "engine_data": {
            "engine": "linear",
            "policy_model_id": INK_DECISION_SMALL,
            "checkpoint_revision": "phase18-full-kd",
            "manifest_sha256": "cb55c8ddb04bcd61f0fd7109dbd5a65e1e93516a82586d43984686e8baf87102",
        }
    }

    s2_id, _ = compute_evidence_identity(site, s2_payload)
    full_id, _ = compute_evidence_identity(site, full_payload)
    assert s2_id != full_id, "Changing checkpoint revision must yield distinct evidence identities"


def test_policy_model_engine_small_regression():
    """Verify that PolicyModelEngine with Small produces the corrected Full KD metadata and valid proposals."""
    spec = get_model_spec(INK_DECISION_SMALL)
    assert spec.checkpoint_revision == "phase18-full-kd"

    engine = PolicyModelEngine(model="small")
    site = DecisionSite(name="reg_site", state_schema={"text": "string"}, choices=("approve", "deny"))
    rows = [
        {"state": {"text": "please approve"}, "choice": "approve"},
        {"state": {"text": "please deny"}, "choice": "deny"},
    ]
    payload = engine.compile(site, rows)
    assert payload["policy_model_id"] == INK_DECISION_SMALL
    assert payload["checkpoint_revision"] == "phase18-full-kd"
    assert payload["manifest_sha256"] == spec.manifest_sha256

    proposal = engine.backend.propose({"text": "urgent billing refund"}, ["approve", "deny"])
    assert proposal.choice in ["approve", "deny"]
    assert 0.0 <= proposal.confidence <= 1.0
    assert 0.0 <= proposal.ambiguity <= 1.0
    assert proposal.familiarity is None


def test_large_missing_dependency_actionable_error(monkeypatch):
    """When gliner2 is unavailable, loading Large must raise an actionable error."""
    import sys
    monkeypatch.setitem(sys.modules, "gliner2", None)

    backend = InkDecisionLargeBackend()
    with pytest.raises(RuntimeError) as exc_info:
        backend.load()
    assert "install ink[large]" in str(exc_info.value).lower()


def test_policy_model_engine_preserves_site_instructions():
    """Verify that when a DecisionSite supplies explicit instructions, PolicyModelEngine uses them unchanged."""
    custom_ins = "Route security requests to triage team immediately."
    site = DecisionSite("sec_site", state_schema={"text": "string"}, choices=("triage", "ignore"), instructions=custom_ins)
    engine = PolicyModelEngine(model="small")
    payload = engine.compile(site, [{"state": {"text": "unauthorized ssh access"}, "choice": "triage"}])
    assert payload["question"]["instructions"] == custom_ins

    # Generic fallback when site has no instructions
    site_no_ins = DecisionSite("no_ins_site", state_schema={"text": "string"}, choices=("triage", "ignore"))
    payload2 = engine.compile(site_no_ins, [{"state": {"text": "unauthorized ssh access"}, "choice": "triage"}])
    assert payload2["question"]["instructions"] == "Choose the correct decision."

