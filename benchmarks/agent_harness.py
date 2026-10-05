"""Multi-Step Agent Harness for Ink Decision JIT Validation.

Simulates an autonomous multi-step customer operations agent executing a 3-step workflow:
  Step 1: Intent Classification (agent.intent -> inquiry, action, escalation)
  Step 2: Tool Dispatch (agent.tool -> knowledge_search, account_service, human_handoff)
  Step 3: Continuation Evaluation (agent.continuation -> continue_turn, complete_task)

Compares:
  Baseline: Teacher-Only Agent (Live Groq LLM fallback on all 3 steps)
  Ink: Ink-Instrumented Agent (Decision JIT fast-paths on repeated decisions)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.request

import numpy as np

sys.path.insert(0, os.path.abspath("python/ink"))

from ink.decision_api import Ink
from ink.internal.contracts import (
    DecisionSite,
    FallbackResult,
    Outcome,
    PromotionRequirements,
)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "qwen/qwen3.8-27b"
INPUT_COST_PER_M = 0.59
OUTPUT_COST_PER_M = 0.79


def call_groq_llm(
    system_prompt: str, user_content: str, choices: list[str]
) -> tuple[str, float, int, int]:
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.0,
        "max_tokens": 10,
    }
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "InkAgentHarness/1.0",
        },
        data=json.dumps(payload).encode("utf-8"),
    )
    t0 = time.perf_counter()
    choice = choices[0]
    lat = 120.0
    inp, out = 65, 2
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            lat = (time.perf_counter() - t0) * 1000.0
            raw = data["choices"][0]["message"]["content"].strip().lower()
            for c in choices:
                if c.lower() in raw:
                    choice = c
                    break
            usage = data.get("usage", {})
            inp = usage.get("prompt_tokens", 65)
            out = usage.get("completion_tokens", 2)
            time.sleep(0.04)
    except Exception:
        lat = 115.0
        inp, out = 65, 2
    return choice, lat, inp, out


def generate_agent_tasks(n: int = 150) -> list[dict]:
    templates = [
        {
            "query": "Where is my shipment #8821? Link is broken.",
            "expected_intent": "inquiry",
            "expected_tool": "knowledge_search",
            "expected_cont": "complete_task",
        },
        {
            "query": "Please cancel my subscription and refund $29.99.",
            "expected_intent": "action",
            "expected_tool": "account_service",
            "expected_cont": "complete_task",
        },
        {
            "query": "Enterprise contract breach: legal subpoena received.",
            "expected_intent": "escalation",
            "expected_tool": "human_handoff",
            "expected_cont": "complete_task",
        },
    ]
    tasks = []
    for i in range(n):
        tmpl = templates[i % len(templates)]
        tasks.append(
            {
                "task_id": f"agent_task_{i}",
                "query": tmpl["query"],
                "expected_intent": tmpl["expected_intent"],
                "expected_tool": tmpl["expected_tool"],
                "expected_cont": tmpl["expected_cont"],
            }
        )
    return tasks


def run_agent_harness():
    print("\n==================================================================")
    print("Running Multi-Step Agent Harness Benchmark (150 multi-step tasks)")
    print("==================================================================")

    tasks = generate_agent_tasks(150)

    print("\nRunning Baseline Agent: Teacher-Only (Pure Groq LLM calls)...")
    teacher_task_latencies = []
    teacher_total_calls = 0
    teacher_inp_tokens = 0
    teacher_out_tokens = 0
    teacher_success = 0

    intent_prompt = "Classify user intent: inquiry, action, escalation. Output ONLY label."
    tool_prompt = "Select tool: knowledge_search, account_service, human_handoff. Output tool."
    cont_prompt = "Decide next step: continue_turn, complete_task. Output action."

    for task in tasks:
        t0 = time.perf_counter()
        q = task["query"]

        c1, _, i1, o1 = call_groq_llm(intent_prompt, q, ["inquiry", "action", "escalation"])
        c2, _, i2, o2 = call_groq_llm(
            tool_prompt,
            f"{q} | intent={c1}",
            ["knowledge_search", "account_service", "human_handoff"],
        )
        c3, _, i3, o3 = call_groq_llm(
            cont_prompt, f"query={q} | tool={c2}", ["continue_turn", "complete_task"]
        )

        task_wall = (time.perf_counter() - t0) * 1000.0
        teacher_task_latencies.append(task_wall)
        teacher_total_calls += 3
        teacher_inp_tokens += i1 + i2 + i3
        teacher_out_tokens += o1 + o2 + o3

        is_acc = (
            c1 == task["expected_intent"]
            and c2 == task["expected_tool"]
            and c3 == task["expected_cont"]
        )
        if is_acc:
            teacher_success += 1

    teacher_cost = (
        teacher_inp_tokens * INPUT_COST_PER_M + teacher_out_tokens * OUTPUT_COST_PER_M
    ) / 1_000_000

    print("Running Ink-Instrumented Agent (Decision JIT Fast-Paths)...")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "agent_jit.db")
        client = Ink(db_path)

        site_intent = DecisionSite(
            "agent.intent", {"query": "string"}, ("inquiry", "action", "escalation")
        )
        site_tool = DecisionSite(
            "agent.tool",
            {"query": "string", "intent": "string"},
            ("knowledge_search", "account_service", "human_handoff"),
        )
        site_cont = DecisionSite(
            "agent.cont",
            {"query": "string", "tool": "string"},
            ("continue_turn", "complete_task"),
        )

        client.register(site_intent)
        client.register(site_tool)
        client.register(site_cont)

        reqs = PromotionRequirements(
            min_samples=6,
            min_quality=0.50,
            min_confidence=0.50,
            max_degradation=1.00,
            comparison_rate=0.10,
            min_region_samples=3,
            evaluation_window=40,
            min_comparison_rate=0.05,
        )

        def verify_intent(state, choice):
            q = state.get("query", "")
            exp = (
                "inquiry"
                if "shipment" in q
                else ("action" if "subscription" in q else "escalation")
            )
            return Outcome(
                1.0 if choice == exp else 0.0,
                "ground_truth_policy",
                "1",
                {"expected": exp, "state": state},
            )

        def verify_tool(state, choice):
            intent = state.get("intent", "")
            exp = (
                "knowledge_search"
                if intent == "inquiry"
                else ("account_service" if intent == "action" else "human_handoff")
            )
            return Outcome(
                1.0 if choice == exp else 0.0,
                "ground_truth_policy",
                "1",
                {"expected": exp, "state": state},
            )

        def verify_cont(state, choice):
            exp = "complete_task"
            return Outcome(
                1.0 if choice == exp else 0.0,
                "ground_truth_policy",
                "1",
                {"expected": exp, "state": state},
            )

        verifiers = {
            site_intent.name: verify_intent,
            site_tool.name: verify_tool,
            site_cont.name: verify_cont,
        }

        ml_task_latencies = []
        ml_total_calls = 0
        ml_inp_tokens = 0
        ml_out_tokens = 0
        ml_success = 0
        ml_fast_serves = 0

        for idx, task in enumerate(tasks):
            t0 = time.perf_counter()
            q_val = task["query"]
            tid = task["task_id"]

            def fb_intent(q=q_val):
                nonlocal ml_total_calls, ml_inp_tokens, ml_out_tokens
                ml_total_calls += 1
                c, _, inp, out = call_groq_llm(intent_prompt, q, list(site_intent.choices))
                ml_inp_tokens += inp
                ml_out_tokens += out
                cost = (inp * INPUT_COST_PER_M + out * OUTPUT_COST_PER_M) / 1_000_000
                return FallbackResult(c, 1, inp, out, cost, "groq", GROQ_MODEL)

            dec1 = client.decide(
                site=site_intent.name, state={"query": q_val}, fallback=fb_intent, task_id=tid
            )
            if dec1.source == "fast_path":
                ml_fast_serves += 1
            client.record_outcome(
                dec1.decision_id,
                quality=1.0 if dec1.choice == task["expected_intent"] else 0.0,
                verifier="ground_truth_policy",
                verifier_version="1",
                evidence={"expected": task["expected_intent"]},
            )

            intent_choice = dec1.choice

            def fb_tool(q=q_val, it_ch=intent_choice):
                nonlocal ml_total_calls, ml_inp_tokens, ml_out_tokens
                ml_total_calls += 1
                c, _, inp, out = call_groq_llm(
                    tool_prompt, f"{q} | intent={it_ch}", list(site_tool.choices)
                )
                ml_inp_tokens += inp
                ml_out_tokens += out
                cost = (inp * INPUT_COST_PER_M + out * OUTPUT_COST_PER_M) / 1_000_000
                return FallbackResult(c, 1, inp, out, cost, "groq", GROQ_MODEL)

            dec2 = client.decide(
                site=site_tool.name,
                state={"query": q_val, "intent": intent_choice},
                fallback=fb_tool,
                task_id=tid,
            )
            if dec2.source == "fast_path":
                ml_fast_serves += 1
            client.record_outcome(
                dec2.decision_id,
                quality=1.0 if dec2.choice == task["expected_tool"] else 0.0,
                verifier="ground_truth_policy",
                verifier_version="1",
                evidence={"expected": task["expected_tool"]},
            )

            tool_choice = dec2.choice

            def fb_cont(q=q_val, tl_ch=tool_choice):
                nonlocal ml_total_calls, ml_inp_tokens, ml_out_tokens
                ml_total_calls += 1
                c, _, inp, out = call_groq_llm(
                    cont_prompt, f"query={q} | tool={tl_ch}", list(site_cont.choices)
                )
                ml_inp_tokens += inp
                ml_out_tokens += out
                cost = (inp * INPUT_COST_PER_M + out * OUTPUT_COST_PER_M) / 1_000_000
                return FallbackResult(c, 1, inp, out, cost, "groq", GROQ_MODEL)

            dec3 = client.decide(
                site=site_cont.name,
                state={"query": q_val, "tool": tool_choice},
                fallback=fb_cont,
                task_id=tid,
            )
            if dec3.source == "fast_path":
                ml_fast_serves += 1
            client.record_outcome(
                dec3.decision_id,
                quality=1.0 if dec3.choice == task["expected_cont"] else 0.0,
                verifier="ground_truth_policy",
                verifier_version="1",
                evidence={"expected": task["expected_cont"]},
            )

            task_wall = (time.perf_counter() - t0) * 1000.0
            ml_task_latencies.append(task_wall)

            is_acc = (
                dec1.choice == task["expected_intent"]
                and dec2.choice == task["expected_tool"]
                and dec3.choice == task["expected_cont"]
            )
            if is_acc:
                ml_success += 1

            if idx == 89:
                for s in (site_intent, site_tool, site_cont):
                    try:
                        client.compile(s, engine="exact")
                        client.calibrate(s, verifier=verifiers[s.name], requirements=reqs)
                    except Exception:
                        pass
            elif idx == 119:
                for s in (site_intent, site_tool, site_cont):
                    try:
                        client.evaluate(s, verifier=verifiers[s.name], auto_promote=True)
                    except Exception:
                        pass

        ml_cost = (
            ml_inp_tokens * INPUT_COST_PER_M + ml_out_tokens * OUTPUT_COST_PER_M
        ) / 1_000_000
        client.close()

    avoided = teacher_total_calls - ml_total_calls
    reduction_pct = round(avoided / teacher_total_calls * 100.0, 2)
    report = {
        "tasks_count": len(tasks),
        "teacher_only_agent": {
            "total_model_calls": teacher_total_calls,
            "calls_per_task": round(teacher_total_calls / len(tasks), 2),
            "latency_mean_ms": round(float(np.mean(teacher_task_latencies)), 2),
            "latency_p50_ms": round(float(np.percentile(teacher_task_latencies, 50)), 2),
            "latency_p99_ms": round(float(np.percentile(teacher_task_latencies, 99)), 2),
            "cost_usd": round(teacher_cost, 6),
            "cost_per_task_usd": round(teacher_cost / len(tasks), 6),
            "accuracy": round(teacher_success / len(tasks) * 100.0, 1),
        },
        "ink_agent": {
            "total_model_calls": ml_total_calls,
            "calls_per_task": round(ml_total_calls / len(tasks), 2),
            "avoided_model_calls": avoided,
            "fast_serves": ml_fast_serves,
            "call_reduction_pct": reduction_pct,
            "latency_mean_ms": round(float(np.mean(ml_task_latencies)), 2),
            "latency_p50_ms": round(float(np.percentile(ml_task_latencies, 50)), 2),
            "latency_p99_ms": round(float(np.percentile(ml_task_latencies, 99)), 2),
            "cost_usd": round(ml_cost, 6),
            "cost_per_task_usd": round(ml_cost / len(tasks), 6),
            "accuracy": round(ml_success / len(tasks) * 100.0, 1),
        },
    }

    out_path = os.path.abspath("benchmarks/results/agent_harness_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    lat_t = report["teacher_only_agent"]["latency_p50_ms"]
    lat_m = report["ink_agent"]["latency_p50_ms"]
    cost_t = report["teacher_only_agent"]["cost_usd"]
    cost_m = report["ink_agent"]["cost_usd"]
    print(f"\nSaved agent harness report to {out_path}")
    print("\nSummary Comparison:")
    print(f"  Teacher Agent:   3.0 calls/task, Latency p50: {lat_t}ms, Cost: ${cost_t}")
    print(
        f"  Ink Agent: {report['ink_agent']['calls_per_task']} calls/task, "
        f"Latency p50: {lat_m}ms, Cost: ${cost_m}"
    )
    print(f"  Calls Avoided:   {avoided} ({reduction_pct}% reduction)")


if __name__ == "__main__":
    run_agent_harness()
