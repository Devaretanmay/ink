"""Tests for trace ingestion and normalization across canonical, OTel, LangSmith, and LiteLLM."""

from __future__ import annotations

import json

from ink.discovery import (
    CanonicalTrace,
    normalize_canonical,
    normalize_langsmith,
    normalize_litellm,
    normalize_opentelemetry,
    stream_traces,
)


def test_normalize_canonical():
    raw = {
        "callsite": "support.route",
        "state": {"intent": "billing", "amount": 42},
        "choices": ["refund", "agent", "faq"],
        "choice": "refund",
        "latency_ms": 110.5,
        "cost_usd": 0.0004,
        "model": "qwen3.8-27b",
        "outcome": {"quality": 1.0, "verifier": "billing_system"},
        "timestamp": 1700000000.0,
    }
    trace = normalize_canonical(raw)
    assert trace is not None
    assert trace.callsite == "support.route"
    assert trace.state == {"intent": "billing", "amount": 42}
    assert trace.choice == "refund"
    assert trace.latency_ms == 110.5
    assert trace.cost_usd == 0.0004
    assert trace.model == "qwen3.8-27b"
    assert trace.outcome == {"quality": 1.0, "verifier": "billing_system"}


def test_normalize_opentelemetry_span():
    raw = {
        "name": "agent.tool_select",
        "start_time_unix_nano": 1700000000000000000,
        "end_time_unix_nano": 1700000000150000000,
        "attributes": {
            "gen_ai.system": "openai",
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.prompt": "Which tool should be called for order lookup?",
            "gen_ai.completion": "database_lookup",
            "llm.cost": 0.00035,
        },
        "status": {"code": 1},
    }
    trace = normalize_opentelemetry(raw)
    assert trace is not None
    assert trace.callsite in ("openai", "agent.tool_select")
    assert trace.choice == "database_lookup"
    assert trace.model == "gpt-4o"
    assert trace.latency_ms == 150.0
    assert trace.cost_usd == 0.00035
    assert trace.outcome is not None
    assert trace.outcome["quality"] == 1.0


def test_normalize_langsmith_run():
    raw = {
        "name": "ticket_classification",
        "run_type": "llm",
        "inputs": {"text": "My package arrived damaged."},
        "outputs": {"choice": "replace_item"},
        "start_time": 1700000000.0,
        "end_time": 1700000000.22,
        "extra": {
            "metadata": {
                "model": "claude-3-5-sonnet",
                "total_cost": 0.0008,
            }
        },
        "feedback": [{"key": "user_rating", "score": 1.0}],
    }
    trace = normalize_langsmith(raw)
    assert trace is not None
    assert trace.callsite == "ticket_classification"
    assert trace.state == {"text": "My package arrived damaged."}
    assert trace.choice == "replace_item"
    assert trace.model == "claude-3-5-sonnet"
    assert trace.latency_ms == 220.0
    assert trace.cost_usd == 0.0008
    assert trace.outcome == {"quality": 1.0, "verifier": "langsmith_feedback"}


def test_normalize_litellm_proxy_log():
    raw = {
        "model": "groq/llama-3.3-70b",
        "messages": [{"role": "user", "content": "cancel order"}],
        "response": {
            "choices": [{"message": {"content": "cancel_order_tool"}}]
        },
        "response_cost": 0.00025,
        "response_time_ms": 95.0,
        "timestamp": 1700000000.0,
        "status": "success",
        "metadata": {"callsite": "agent.tool_dispatch"},
    }
    trace = normalize_litellm(raw)
    assert trace is not None
    assert trace.callsite == "agent.tool_dispatch"
    assert trace.choice == "cancel_order_tool"
    assert trace.model == "groq/llama-3.3-70b"
    assert trace.latency_ms == 95.0
    assert trace.cost_usd == 0.00025
    assert trace.outcome == {"quality": 1.0, "verifier": "litellm_status"}


def test_streaming_ingestion_multi_format(tmp_path):
    records = [
        # Canonical
        {
            "callsite": "site_a",
            "state": {"query": "hello"},
            "choice": "greet",
            "latency_ms": 100.0,
            "cost_usd": 0.0001,
        },
        # OpenTelemetry
        {
            "name": "site_b",
            "start_time_unix_nano": 1000000000,
            "end_time_unix_nano": 1100000000,
            "attributes": {"gen_ai.prompt": "ping", "gen_ai.completion": "pong"},
        },
        # LangSmith
        {
            "name": "site_c",
            "inputs": {"arg": 1},
            "outputs": {"output": "result"},
            "start_time": 1.0,
            "end_time": 1.1,
        },
    ]
    jsonl_path = tmp_path / "mixed_traces.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    streamed = list(stream_traces(jsonl_path))
    assert len(streamed) == 3
    assert all(isinstance(t, CanonicalTrace) for t in streamed)
    assert streamed[0].choice == "greet"
    assert streamed[1].choice == "pong"
    assert streamed[2].choice == "result"
