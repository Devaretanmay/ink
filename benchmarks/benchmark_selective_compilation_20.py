"""20-site selective compilation and Best-N study: compares compile-all vs top-1, top-3, top-5, and recommended-only strategies."""

from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python/microloop"))

from microloop.decision_api import DecisionSite, FallbackResult, Microloop, Outcome, PromotionRequirements


def generate_fleet_contracts() -> list[dict]:
    configs = [
        # Prime (1-3)
        {"name": "site_01_support_billing", "choices": ["refund", "invoice", "agent"], "rep": 0.88, "traffic": 5000, "has_v": True, "vol": 0.0},
        {"name": "site_02_tool_navigation", "choices": ["search", "filter", "detail"], "rep": 0.85, "traffic": 4500, "has_v": True, "vol": 0.0},
        {"name": "site_03_auth_permissions", "choices": ["allow", "deny"], "rep": 0.92, "traffic": 4000, "has_v": True, "vol": 0.0},
        # Good (4-6)
        {"name": "site_04_query_routing", "choices": ["replica_a", "replica_b", "primary"], "rep": 0.72, "traffic": 2500, "has_v": True, "vol": 0.02},
        {"name": "site_05_email_labeler", "choices": ["inbox", "archive", "spam"], "rep": 0.68, "traffic": 2000, "has_v": True, "vol": 0.02},
        {"name": "site_06_cache_decision", "choices": ["hit", "miss"], "rep": 0.75, "traffic": 1800, "has_v": True, "vol": 0.01},
        # No verifier (7-9)
        {"name": "site_07_unverified_sentiment", "choices": ["pos", "neu", "neg"], "rep": 0.80, "traffic": 2000, "has_v": False, "vol": 0.0},
        {"name": "site_08_unverified_style", "choices": ["formal", "casual"], "rep": 0.82, "traffic": 1500, "has_v": False, "vol": 0.0},
        {"name": "site_09_unverified_tone", "choices": ["warm", "concise"], "rep": 0.78, "traffic": 1200, "has_v": False, "vol": 0.0},
        # Low repetition (10-12)
        {"name": "site_10_unique_search", "choices": ["web", "docs"], "rep": 0.08, "traffic": 3000, "has_v": True, "vol": 0.0},
        {"name": "site_11_open_q_routing", "choices": ["agent_1", "agent_2"], "rep": 0.11, "traffic": 2500, "has_v": True, "vol": 0.0},
        {"name": "site_12_custom_filter", "choices": ["pass", "drop"], "rep": 0.14, "traffic": 2000, "has_v": True, "vol": 0.0},
        # High entropy (13-15)
        {"name": "site_13_wide_category", "choices": [f"cat_{i}" for i in range(25)], "rep": 0.50, "traffic": 1500, "has_v": True, "vol": 0.0},
        {"name": "site_14_multitool_pick", "choices": [f"tool_{i}" for i in range(20)], "rep": 0.45, "traffic": 1200, "has_v": True, "vol": 0.0},
        {"name": "site_15_action_dispatch", "choices": [f"act_{i}" for i in range(18)], "rep": 0.40, "traffic": 1000, "has_v": True, "vol": 0.0},
        # Low traffic (16-18)
        {"name": "site_16_admin_reset", "choices": ["yes", "no"], "rep": 0.90, "traffic": 50, "has_v": True, "vol": 0.0},
        {"name": "site_17_rare_error_triage", "choices": ["ignore", "alert"], "rep": 0.85, "traffic": 40, "has_v": True, "vol": 0.0},
        {"name": "site_18_system_shutdown", "choices": ["graceful", "force"], "rep": 0.95, "traffic": 20, "has_v": True, "vol": 0.0},
        # High volatility (19-20)
        {"name": "site_19_volatile_pricing", "choices": ["tier_1", "tier_2", "tier_3"], "rep": 0.80, "traffic": 3000, "has_v": True, "vol": 0.45},
        {"name": "site_20_volatile_routing", "choices": ["route_a", "route_b"], "rep": 0.82, "traffic": 2500, "has_v": True, "vol": 0.40},
    ]
    return configs


def run_selective_compilation_study():
    print("=" * 60)
    print("20-SITE SELECTIVE COMPILATION & BEST-N STUDY")
    print("=" * 60)

    fleet = generate_fleet_contracts()
    db_root = Path(".microloop/selective_study")
    if db_root.exists():
        shutil.rmtree(db_root)
    db_root.mkdir(parents=True, exist_ok=True)

    # Compute expected net value for ranking
    ranked = []
    for cfg in fleet:
        num_c = len(cfg["choices"])
        qual_calls = 50 * max(2, min(num_c, 10))
        repeatable_calls = cfg["traffic"] * cfg["rep"]
        has_v = cfg["has_v"]
        vol = cfg["vol"]
        if not has_v or vol > 0.20 or cfg["rep"] < 0.20 or num_c > 15:
            expected_net = -qual_calls
            rec = "ignore"
        else:
            net_avoided = max(0, int(repeatable_calls * 0.95 - qual_calls))
            expected_net = net_avoided
            rec = "compile"
        ranked.append({**cfg, "qual_calls": qual_calls, "expected_net": expected_net, "rec": rec})

    ranked.sort(key=lambda x: x["expected_net"], reverse=True)

    strategies = {
        "top_1": [ranked[0]["name"]],
        "top_3": [r["name"] for r in ranked[:3]],
        "top_5": [r["name"] for r in ranked[:5]],
        "recommended_only": [r["name"] for r in ranked if r["rec"] == "compile"],
        "compile_all": [r["name"] for r in ranked],
    }

    report = {}

    for strat_name, target_sites in strategies.items():
        db_path = db_root / f"{strat_name}.db"
        with Microloop(str(db_path)) as client:
            for cfg in fleet:
                site = DecisionSite(
                    name=cfg["name"],
                    state_schema={"query": "string"},
                    choices=tuple(cfg["choices"]),
                )
                client.register(site)

            for cfg in fleet:
                s_name = cfg["name"]
                choices = cfg["choices"]
                for i in range(300):
                    c = choices[i % len(choices)]
                    res = client.decide(
                        site=s_name,
                        state={"query": f"input_{i % 2}" if (i % 10 < int(cfg['rep'] * 10)) else f"input_{i}"},
                        task_id=f"obs_{s_name}_{i}",
                        fallback=lambda choice=c: FallbackResult(choice, model_calls=1, cost=0.0004),
                    )
                    client.record_outcome(
                        decision_id=res.decision_id,
                        quality=1.0 if cfg["has_v"] else 0.0,
                        verifier="study_verifier",
                        verifier_version="1",
                        evidence={"test": True},
                    )

            def verifier_func(state, choice):
                return Outcome(
                    quality=1.0,
                    verifier="study_verifier",
                    verifier_version="1",
                    evidence={"valid": True},
                )

            reqs = PromotionRequirements(10, 0.5, 0.5, 0.6, 0.25, 5, 100)
            target_site_objs = [client._resolve(name) for name in target_sites]

            client.maintenance(sites=target_site_objs, verifier=verifier_func, requirements=reqs, engine="exact")

            for name in target_sites:
                site_cfg = next(c for c in fleet if c["name"] == name)
                for i in range(100):
                    c = site_cfg["choices"][i % len(site_cfg["choices"])]
                    res = client.decide(
                        site=name,
                        state={"query": f"input_{i % 2}"},
                        task_id=f"shadow_{name}_{i}",
                        fallback=lambda choice=c: FallbackResult(choice, model_calls=1, cost=0.0004),
                    )
                    client.record_outcome(
                        decision_id=res.decision_id,
                        quality=1.0 if site_cfg["has_v"] else 0.0,
                        verifier="study_verifier",
                        verifier_version="1",
                        evidence={"test": True},
                    )

            # Tick 2: Evaluate shadow and promote verified sites
            client.maintenance(sites=target_site_objs, verifier=verifier_func, requirements=reqs, engine="exact")

            compiled_count = sum(1 for name in target_sites if client.inspect(name)["state"] == "ACTIVE")
            qual_calls_total = sum(next(c for c in fleet if c["name"] == name).get("qual_calls", 150) for name in target_sites)

            # 4. Simulate active serving traffic (20,000 decisions across fleet)
            avoided_calls = 0
            fallback_calls = 0
            false_serves = 0

            for step in range(20_000):
                cfg = fleet[step % len(fleet)]
                is_compiled = cfg["name"] in target_sites
                is_repeat = (step % 100) < int(cfg["rep"] * 100)
                query = f"input_{step % 2}" if is_repeat else f"novel_{step}"
                expected_choice = cfg["choices"][0]

                res = client.decide(
                    site=cfg["name"],
                    state={"query": query},
                    fallback=lambda ch=expected_choice: FallbackResult(ch, model_calls=1, cost=0.0004),
                )
                if res.source == "fast_path":
                    avoided_calls += 1
                    if cfg["vol"] > 0.20 and (step % 5 == 0):
                        false_serves += 1
                else:
                    fallback_calls += 1

            db_size_kb = round(db_path.stat().st_size / 1024, 1) if db_path.exists() else 0.0
            net_avoided = max(0, avoided_calls - qual_calls_total)
            net_cost_savings = round(net_avoided * 0.0004, 2)

            report[strat_name] = {
                "strategy": strat_name,
                "compiled_sites": compiled_count,
                "qualification_overhead_calls": qual_calls_total,
                "served_fast_paths": avoided_calls,
                "net_calls_avoided": net_avoided,
                "net_cost_savings_usd": net_cost_savings,
                "false_serves": false_serves,
                "database_size_kb": db_size_kb,
            }

            print(f"\nStrategy: {strat_name:18}")
            print(f"  Compiled Sites      : {compiled_count:2d} / 20")
            print(f"  Qual Overhead Calls : {qual_calls_total:,}")
            print(f"  Served Fast Paths   : {avoided_calls:,}")
            print(f"  Net Calls Avoided   : {net_avoided:,}")
            print(f"  Net Cost Saved      : ${net_cost_savings:,.2f}")
            print(f"  False Serves        : {false_serves}")
            print(f"  DB Size             : {db_size_kb:.1f} KB")

    out_path = Path(__file__).resolve().parent / "results/selective_compilation_20.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nSaved report to {out_path}")


if __name__ == "__main__":
    run_selective_compilation_study()
