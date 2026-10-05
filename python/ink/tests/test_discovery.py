"""Tests for DecisionSite discovery tool."""

from __future__ import annotations

import json

from ink.discovery import CandidateSite, discover_from_file, discover_from_traces


def test_discover_from_traces_categorization():
    traces = []
    # Site 1: High repetition bounded routing
    for i in range(100):
        traces.append(
            {
                "site_name": "support_routing",
                "state": {"intent": f"category_{i % 3}"},
                "choice": "refund" if (i % 3 == 0) else "specialist",
                "outcome": 1,
                "elapsed_ms": 250.0,
                "cost": 0.005,
                "timestamp": 1000 + i * 10,
            }
        )

    # Site 2: Free text unstructured generation
    for i in range(100):
        traces.append(
            {
                "site_name": "free_text_summary",
                "prompt": f"Summarize user query {i}",
                "response": f"Summary text result with unique variance {i}",
                "elapsed_ms": 600.0,
                "cost": 0.01,
                "timestamp": 1000 + i * 10,
            }
        )

    # Site 3: Low repetition search
    for i in range(100):
        traces.append(
            {
                "site_name": "unique_search",
                "query": f"search query random {i}",
                "choice": "search",
                "outcome": 1,
                "elapsed_ms": 100.0,
                "cost": 0.001,
                "timestamp": 1000 + i * 10,
            }
        )

    candidates = discover_from_traces(traces)
    assert len(candidates) == 3
    site_map = {c.site_name: c for c in candidates}

    assert site_map["support_routing"].recommendation == "compile"
    assert site_map["support_routing"].repetition_rate > 0.90
    assert site_map["support_routing"].verifiability == 1.0

    assert site_map["free_text_summary"].recommendation == "ignore"
    assert "High output entropy" in site_map["free_text_summary"].reason

    assert site_map["unique_search"].recommendation == "ignore"
    assert "Low repetition rate" in site_map["unique_search"].reason


def test_discover_from_file(tmp_path):
    traces = [
        {
            "template_name": "classifier",
            "state": {"text": "hello"},
            "choice": "greet",
            "success": True,
            "cost": 0.002,
        }
        for _ in range(50)
    ]
    file_path = tmp_path / "traces.jsonl"
    with open(file_path, "w", encoding="utf-8") as f:
        for t in traces:
            f.write(json.dumps(t) + "\n")

    candidates = discover_from_file(file_path)
    assert len(candidates) == 1
    assert isinstance(candidates[0], CandidateSite)
    assert candidates[0].site_name == "classifier"
    assert candidates[0].recommendation == "compile"
