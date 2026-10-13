from __future__ import annotations

import sys
import threading

from ink import DecisionSite, Ink
from ink.internal.contracts import canonical, digest
from ink.internal.engines import (
    ExactEngine,
    LinearClassifierEngine,
    PolicyModelEngine,
    PolicyProposal,
)


class CountingExact(ExactEngine):
    def __init__(self):
        self.calls = 0

    def predict(self, payload, state):
        self.calls += 1
        return super().predict(payload, state)


class CountingLinear(LinearClassifierEngine):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def predict(self, payload, state):
        self.calls += 1
        return payload["choice"], payload.get("probability", 1.0)


def _install_artifact(client, site, engine_data, states, *, status="ACTIVE", suffix="x"):
    coverage = {
        canonical(state): {"choice": engine_data.get("choice", "allow"), "confidence": 1.0}
        for state in states
    }
    payload = {
        "site": site.version,
        "choices": list(site.choices),
        "engine_data": engine_data,
        "coverage": coverage,
    }
    profile = {
        "requirements": {"min_confidence": 0.0, "comparison_rate": 0.0},
        "coverage": coverage,
    }
    profile["id"] = digest(profile)
    client.store.save_artifact(
        f"artifact-{suffix}",
        site.version,
        canonical(payload),
        digest(payload),
        status,
        1.0,
        canonical(profile),
        None,
    )


def test_gate_serving_path_exact_linear_host_and_unqualified_linear(tmp_path):
    site = DecisionSite("phase22.route", {"kind": "string"}, ("allow", "deny"))
    exact, linear = CountingExact(), CountingLinear()
    host_calls = 0

    def host():
        nonlocal host_calls
        host_calls += 1
        return "deny"

    with Ink(tmp_path / "serving.db", engines=(exact, linear), policy_model=None) as client:
        client.register(site)
        _install_artifact(
            client,
            site,
            {
                "engine": "exact",
                "table": {canonical({"kind": "exact"}): {"choice": "allow", "probability": 1.0}},
            },
            [{"kind": "exact"}],
            suffix="exact",
        )
        _install_artifact(
            client,
            site,
            {"engine": "linear", "choice": "deny", "probability": 1.0},
            [{"kind": "linear"}],
            suffix="linear",
        )

        result = client.decide(site, {"kind": "exact"}, fallback=host)
        assert result.source == "fast_path"
        assert result.receipt["engine"] == "exact"
        assert linear.calls == 0
        assert host_calls == 0

        result = client.decide(site, {"kind": "linear"}, fallback=host)
        assert result.source == "fast_path"
        assert result.receipt["engine"] == "linear"
        assert exact.calls == 1  # Exact coverage miss does not invoke Exact engine.
        assert linear.calls == 1
        assert host_calls == 0

        result = client.decide(site, {"kind": "other"}, fallback=host)
        assert result.source == "fallback"
        assert host_calls == 1

        client.store.demote_artifact("artifact-linear", new_status="SHADOW")
        result = client.decide(site, {"kind": "linear"}, fallback=host)
        assert result.source == "fallback"
        assert result.fallback_reason == "shadow"
        assert host_calls == 2


class BlockingSmallBackend:
    model_id = "ink-decision-small"
    model_version = "test"
    checkpoint_revision = "test-rev"
    manifest_sha256 = "test-manifest"
    backend_name = "test"
    checkpoint = "test"

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def load(self):
        return None

    def propose(self, state, choices, instructions=None):
        self.started.set()
        assert self.release.wait(2.0)
        return PolicyProposal(choices[0], {choices[0]: 0.8, choices[1]: 0.2}, 0.8, 0.4, None, 0.7)

    def representation(self, state, choices, instructions=None):
        return [1.0, 0.0, 0.5]

    def close(self):
        return None


def test_gate_small_shadow_persists_without_serving_or_latency(tmp_path):
    site = DecisionSite("phase22.shadow", {"text": "string"}, ("a", "b"))
    backend = BlockingSmallBackend()
    engine = PolicyModelEngine(backend=backend)
    with Ink(
        tmp_path / "shadow.db", engines=(engine,), policy_model="small", policy_shadow=True
    ) as client:
        client.register(site)
        result = client.decide(site, {"text": "new request"}, fallback=lambda: "b")
        assert result.source == "fallback"
        assert backend.started.wait(1.0)
        assert client.policy_inference_count == 0
        backend.release.set()
        client.record_outcome(
            result.decision_id,
            quality=1.0,
            verifier="test",
            verifier_version="1",
            evidence={"expected": "b"},
        )
        client.drain_control_plane(timeout=2.0)
        rows = client.store.get_policy_observations(site.version, verified_only=True)
        assert len(rows) == 1
        assert rows[0]["host_choice"] == "b"
        assert rows[0]["proposed_choice"] == "a"
        assert rows[0]["representation"] == [1.0, 0.0, 0.5]
        assert rows[0]["verified_outcome"]["evidence"]["expected"] == "b"


def test_gate_policy_none_and_large_isolation(tmp_path):
    site = DecisionSite("phase22.none", {"x": "integer"}, ("a", "b"))
    before = {name for name in sys.modules if name == "gliner2" or name.startswith("gliner2.")}
    with Ink(tmp_path / "none.db", policy_model=None) as client:
        result = client.decide(site, {"x": 1}, fallback=lambda: "a")
        client.record_outcome(
            result.decision_id, quality=1.0, verifier="test", verifier_version="1"
        )
        assert client.policy_inference_count == 0
        assert client.store.get_policy_observations(site.version) == []
    after = {name for name in sys.modules if name == "gliner2" or name.startswith("gliner2.")}
    assert after == before


def test_gate_small_compiler_input_is_consumed_but_never_used_as_label(tmp_path):
    site = DecisionSite("phase22.compiler", {"x": "integer"}, ("a", "b"))
    with Ink(tmp_path / "compiler.db", policy_model=None) as client:
        client.register(site)
        for index in range(120):
            state = {"x": index}
            expected = "a" if index % 2 == 0 else "b"
            result = client.decide(
                site, state, task_id=f"task-{index}", fallback=lambda expected=expected: expected
            )
            client.record_outcome(
                result.decision_id,
                quality=1.0,
                verifier="test",
                verifier_version="1",
                evidence={"expected": expected},
            )
            client.store.save_policy_observation(
                decision_id=result.decision_id,
                site_version=site.version,
                state=state,
                host_choice=expected,
                proposed_choice="b" if expected == "a" else "a",  # Deliberately wrong.
                scores={"a": 0.5, "b": 0.5},
                representation=[float(index % 2), float((index + 1) % 2)],
                confidence=0.5,
                ambiguity=1.0,
                act_probability=0.5,
                checkpoint_identity="test-checkpoint",
            )
            client.store.attach_policy_outcome(
                result.decision_id,
                {
                    "quality": 1.0,
                    "verifier": "test",
                    "verifier_version": "1",
                    "evidence": {"expected": expected},
                },
            )

        artifact_id = client.compile(site, engine="linear")
        artifact = client._dispatcher.load_artifact(site.version)
        assert artifact["id"] == artifact_id
        selection = artifact["payload"]["selection_record"]
        assert selection["kind"] == "representation_ablation"
        assert selection["label_source"] == "verified_outcome"
        assert any(candidate["uses_small"] for candidate in selection["candidates"])
        assert artifact["payload"]["engine_data"]["engine"] == "linear"
