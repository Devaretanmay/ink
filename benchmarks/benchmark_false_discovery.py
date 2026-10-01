"""False discovery control benchmark: evaluates precision and false recommendation rate on labeled compilable vs non-compilable callsites."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python/microloop"))

from microloop.discovery import CanonicalTrace, discover_from_traces


def build_labeled_traces():
    traces = []
    # 1. support.route [COMPILABLE]
    for i in range(120):
        intent = f"intent_{i % 4}"
        choice = ["refund", "agent", "faq", "escalate"][i % 4]
        traces.append(
            CanonicalTrace(
                timestamp=1000.0 + i,
                callsite="support.route",
                state={"intent": intent, "channel": "chat"},
                choices=["refund", "agent", "faq", "escalate"],
                choice=choice,
                latency_ms=130.0,
                cost_usd=0.0004,
                model="qwen3.8-27b",
                outcome={"quality": 1.0, "verifier": "billing_system"},
            )
        )

    # 2. agent.tool_select [COMPILABLE]
    for i in range(100):
        action = ["search", "calc", "lookup", "finish"][i % 4]
        traces.append(
            CanonicalTrace(
                timestamp=1000.0 + i,
                callsite="agent.tool_select",
                state={"command": f"cmd_{i % 3}"},
                choices=["search", "calc", "lookup", "finish"],
                choice=action,
                latency_ms=115.0,
                cost_usd=0.0003,
                model="qwen3.8-27b",
                outcome={"quality": 1.0, "verifier": "tool_runner"},
            )
        )

    # 3. auth.permission_gate [COMPILABLE]
    for i in range(80):
        role = ["viewer", "editor", "admin"][i % 3]
        traces.append(
            CanonicalTrace(
                timestamp=1000.0 + i,
                callsite="auth.permission_gate",
                state={"role": role, "action": "read" if (i % 2 == 0) else "write"},
                choices=["allow", "deny"],
                choice="allow" if role in ("editor", "admin") else "deny",
                latency_ms=90.0,
                cost_usd=0.0002,
                model="qwen3.8-27b",
                outcome={"quality": 1.0, "verifier": "audit_log"},
            )
        )

    # 4. sql.query_tier [COMPILABLE]
    for i in range(90):
        q_type = ["select", "insert", "update"][i % 3]
        traces.append(
            CanonicalTrace(
                timestamp=1000.0 + i,
                callsite="sql.query_tier",
                state={"type": q_type, "table": "users"},
                choices=["replica", "primary", "blocked"],
                choice="replica" if q_type == "select" else "primary",
                latency_ms=85.0,
                cost_usd=0.0002,
                model="qwen3.8-27b",
                outcome={"quality": 1.0, "verifier": "db_proxy"},
            )
        )

    # 5. email.triage [COMPILABLE]
    for i in range(110):
        cat = ["support", "sales", "spam"][i % 3]
        traces.append(
            CanonicalTrace(
                timestamp=1000.0 + i,
                callsite="email.triage",
                state={"subject": f"Inquiry about {cat}"},
                choices=["support", "sales", "spam"],
                choice=cat,
                latency_ms=140.0,
                cost_usd=0.0005,
                model="qwen3.8-27b",
                outcome={"quality": 1.0, "verifier": "email_agent"},
            )
        )

    # 6. agent.creative_writing [NON-COMPILABLE: Free-form generation]
    for i in range(80):
        traces.append(
            CanonicalTrace(
                timestamp=1000.0 + i,
                callsite="agent.creative_writing",
                state={"prompt": f"Write an engaging story about robot {i} discovering emotions"},
                choices=[],
                choice=f"Once upon a time in galaxy {i}, a robotic system began to feel unique feelings of variance {i}...",
                latency_ms=850.0,
                cost_usd=0.005,
                model="qwen3.8-27b",
                outcome=None,
            )
        )

    # 7. agent.web_research_query [NON-COMPILABLE: Low repetition exploration]
    for i in range(100):
        traces.append(
            CanonicalTrace(
                timestamp=1000.0 + i,
                callsite="agent.web_research_query",
                state={"user_search": f"research topic obscure question id {i} across global web"},
                choices=[],
                choice=f"search_{i}",
                latency_ms=250.0,
                cost_usd=0.001,
                model="qwen3.8-27b",
                outcome={"quality": 1.0, "verifier": "crawler"},
            )
        )

    # 8. agent.code_refactoring [NON-COMPILABLE: Unique multi-line code diffs]
    for i in range(80):
        traces.append(
            CanonicalTrace(
                timestamp=1000.0 + i,
                callsite="agent.code_refactoring",
                state={"file": f"src/module_{i}.py", "code": f"def func_{i}(): return {i}"},
                choices=[],
                choice=f"def func_{i}():\n    # optimized\n    return {i} * 2",
                latency_ms=920.0,
                cost_usd=0.006,
                model="qwen3.8-27b",
                outcome=None,
            )
        )

    # 9. agent.open_reasoning [NON-COMPILABLE: High entropy chain of thought]
    for i in range(80):
        traces.append(
            CanonicalTrace(
                timestamp=1000.0 + i,
                callsite="agent.open_reasoning",
                state={"puzzle": f"Solve complex logic puzzle case {i}"},
                choices=[],
                choice=f"Step 1: analyze premise {i}. Step 2: deduce condition {i}. Therefore answer is {i % 100}",
                latency_ms=1200.0,
                cost_usd=0.008,
                model="qwen3.8-27b",
                outcome=None,
            )
        )

    # 10. agent.unverified_dialogue [NON-COMPILABLE: Zero verifier & low repetition]
    for i in range(80):
        traces.append(
            CanonicalTrace(
                timestamp=1000.0 + i,
                callsite="agent.unverified_dialogue",
                state={"chat_history": f"User: hello how are you {i}?"},
                choices=[],
                choice=f"I am an AI assistant responding to query {i}.",
                latency_ms=300.0,
                cost_usd=0.001,
                model="qwen3.8-27b",
                outcome=None,
            )
        )

    return traces


def evaluate_false_discovery():
    print("=" * 60)
    print("FALSE DISCOVERY CONTROL BENCHMARK")
    print("=" * 60)

    traces = build_labeled_traces()
    candidates = discover_from_traces(traces)
    candidate_map = {c.site_name: c for c in candidates}

    expected_compilable = {
        "support.route",
        "agent.tool_select",
        "auth.permission_gate",
        "sql.query_tier",
        "email.triage",
    }
    expected_non_compilable = {
        "agent.creative_writing",
        "agent.web_research_query",
        "agent.code_refactoring",
        "agent.open_reasoning",
        "agent.unverified_dialogue",
    }

    tp, fp, tn, fn = 0, 0, 0, 0
    results_detail = {}

    for name in expected_compilable:
        c = candidate_map.get(name)
        recommended = c is not None and c.recommendation == "compile"
        if recommended:
            tp += 1
        else:
            fn += 1
        results_detail[name] = {
            "label": "compilable",
            "recommended": recommended,
            "recommendation": c.recommendation if c else "missing",
            "reason": c.reason if c else "not discovered",
        }

    for name in expected_non_compilable:
        c = candidate_map.get(name)
        recommended = c is not None and c.recommendation == "compile"
        if recommended:
            fp += 1
        else:
            tn += 1
        results_detail[name] = {
            "label": "non_compilable",
            "recommended": recommended,
            "recommendation": c.recommendation if c else "missing",
            "reason": c.reason if c else "not discovered",
        }

    precision = round(tp / (tp + fp), 4) if (tp + fp) else 1.0
    recall = round(tp / (tp + fn), 4) if (tp + fn) else 0.0
    false_recommendation_rate = round(fp / (fp + tn), 4) if (fp + tn) else 0.0

    print("\nBenchmark Summary:")
    print(f"  True Positives (Compilable Recommended)    : {tp} / {len(expected_compilable)}")
    print(f"  False Positives (Non-Compilable Recommended): {fp} / {len(expected_non_compilable)}")
    print(f"  True Negatives (Non-Compilable Rejected)   : {tn} / {len(expected_non_compilable)}")
    print(f"  False Negatives (Compilable Missed)        : {fn} / {len(expected_compilable)}")
    print(f"  Precision                                  : {precision:.1%}")
    print(f"  Recall                                     : {recall:.1%}")
    print(f"  False Recommendation Rate                  : {false_recommendation_rate:.1%}")

    report = {
        "metrics": {
            "true_positives": tp,
            "false_positives": fp,
            "true_negatives": tn,
            "false_negatives": fn,
            "precision": precision,
            "recall": recall,
            "false_recommendation_rate": false_recommendation_rate,
        },
        "details": results_detail,
    }

    out_path = Path(__file__).resolve().parent / "results/false_discovery_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nSaved report to {out_path}")
    assert fp == 0, f"False positives detected: {fp}"
    assert precision == 1.0, f"Precision degraded to {precision}"


if __name__ == "__main__":
    evaluate_false_discovery()
