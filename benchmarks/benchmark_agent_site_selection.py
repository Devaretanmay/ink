"""Agent site selection benchmark demonstrating selective compilation economics."""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

from microloop.discovery import discover_from_traces
from microloop.internal.profiler import profile_history


def generate_agent_traces(n_episodes: int = 500, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    traces = []
    base_intents = ["check_balance", "transfer_funds", "report_fraud", "ask_faq"]
    tools = ["database_query", "web_search", "auth_prompt", "escalate_human"]

    for ep in range(n_episodes):
        now = 1700000000 + ep * 60
        # 1. agent.intent: repeated customer intents (Zipf-like)
        intent_idx = 0 if rng.random() < 0.55 else rng.randint(0, len(base_intents) - 1)
        intent_state = {"intent_category": base_intents[intent_idx]}
        intent_choice = base_intents[intent_idx]
        traces.append(
            {
                "site_name": "agent.intent",
                "state": intent_state,
                "choice": intent_choice,
                "source": "fallback",
                "outcome": {"quality": 1},
                "elapsed": 0.12,
                "elapsed_ms": 120.0,
                "cost": 0.0015,
                "usage": {"cost": 0.0015, "model_calls": 1},
                "timestamp": now,
            }
        )

        # 2. agent.tool: moderate variety of queries
        tool_query = f"query_detail_{rng.randint(0, 150)}"
        tool_choice = tools[rng.randint(0, len(tools) - 1)]
        tool_success = rng.random() < 0.85
        traces.append(
            {
                "site_name": "agent.tool",
                "state": {"query": tool_query, "step": rng.randint(1, 3)},
                "choice": tool_choice,
                "source": "fallback",
                "outcome": {"quality": 1 if tool_success else 0},
                "elapsed": 0.22,
                "elapsed_ms": 220.0,
                "cost": 0.003,
                "usage": {"cost": 0.003, "model_calls": 1},
                "timestamp": now + 5,
            }
        )

        # 3. agent.cont: complex multi-turn state (high entropy, low repetition)
        context_id = f"ctx_{ep}_{rng.randint(1000, 9999)}"
        cont_choice = "continue" if rng.random() < 0.70 else "stop"
        traces.append(
            {
                "site_name": "agent.cont",
                "state": {"context_id": context_id, "history_len": rng.randint(1, 10)},
                "choice": cont_choice,
                "source": "fallback",
                "outcome": {"quality": 1},
                "elapsed": 0.18,
                "elapsed_ms": 180.0,
                "cost": 0.0025,
                "usage": {"cost": 0.0025, "model_calls": 1},
                "timestamp": now + 10,
            }
        )
    return traces


def run_benchmark():
    print("Running Agent Site Selection Benchmark...")
    traces = generate_agent_traces(n_episodes=600, seed=42)

    by_site: dict[str, list[dict]] = {}
    for t in traces:
        by_site.setdefault(t["site_name"], []).append(t)

    site_profiles = {}
    for name, rows in by_site.items():
        prof = profile_history(rows)
        site_profiles[name] = prof.to_dict()

    discovery_candidates = discover_from_traces(traces)
    candidate_summary = [c.to_dict() for c in discovery_candidates]

    # Evaluate selective vs naive compilation economics over 10,000 requests
    traffic_horizon = 10_000
    comp_rate = 0.05

    economics = {}
    for name, prof in site_profiles.items():
        rep = prof["exact_repeat_rate"]
        fb_cost = prof["fallback_cost_per_decision"] or 0.002
        qual_calls = prof["qualification_cost_decisions"]
        qual_cost = qual_calls * fb_cost

        # If compiled:
        avoided = int(traffic_horizon * rep * (1.0 - comp_rate)) if rep > 0.15 else 0
        gross_saved = avoided * fb_cost
        net_saved = gross_saved - qual_cost
        roi_pct = (net_saved / qual_cost * 100.0) if qual_cost > 0 else 0.0

        economics[name] = {
            "repetition_rate": rep,
            "recommendation": prof["recommendation"],
            "qualification_calls": qual_calls,
            "qualification_cost_usd": round(qual_cost, 4),
            "calls_avoided": avoided,
            "gross_savings_usd": round(gross_saved, 2),
            "net_savings_usd": round(net_saved, 2),
            "roi_pct": round(roi_pct, 1),
            "profitable": net_saved > 0,
        }

    # Strategy Comparison:
    # Strategy A: Compile All Sites blindly
    all_net = sum(e["net_savings_usd"] for e in economics.values())
    all_avoided = sum(e["calls_avoided"] for e in economics.values())
    all_qual_cost = sum(e["qualification_cost_usd"] for e in economics.values())

    # Strategy B: Compile ONLY recommended sites (e.g. strong_candidate / compile)
    rec_sites = [
        name for name, e in economics.items()
        if e["recommendation"] == "strong_candidate" or e["repetition_rate"] >= 0.20
    ]
    rec_net = sum(economics[name]["net_savings_usd"] for name in rec_sites)
    rec_avoided = sum(economics[name]["calls_avoided"] for name in rec_sites)
    rec_qual_cost = sum(economics[name]["qualification_cost_usd"] for name in rec_sites)

    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "traces_analyzed": len(traces),
        "site_profiles": site_profiles,
        "discovery_candidates": candidate_summary,
        "per_site_economics": economics,
        "strategies": {
            "strategy_a_compile_all": {
                "compiled_sites": list(economics.keys()),
                "total_calls_avoided": all_avoided,
                "total_qualification_cost_usd": round(all_qual_cost, 2),
                "total_net_savings_usd": round(all_net, 2),
                "efficiency_verdict": (
                    "SUBOPTIMAL: Wasted qualification spend on unrepeatable sites."
                ),
            },
            "strategy_b_selective_compilation": {
                "compiled_sites": rec_sites,
                "rejected_sites": [s for s in economics if s not in rec_sites],
                "total_calls_avoided": rec_avoided,
                "total_qualification_cost_usd": round(rec_qual_cost, 2),
                "total_net_savings_usd": round(rec_net, 2),
                "efficiency_verdict": (
                    "OPTIMAL: Maximize ROI by compiling high-repetition sites and "
                    "refusing unrepeatable sites."
                ),
            },
        },
    }

    out_path = Path("benchmarks/results/agent_site_selection.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Saved agent site selection results to {out_path}")
    print(
        f"Selective Compilation Net Savings: ${rec_net:.2f} "
        f"(vs Compile All: ${all_net:.2f})"
    )


if __name__ == "__main__":
    run_benchmark()
