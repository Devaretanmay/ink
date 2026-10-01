"""Phase 5 Adaptation Benchmark: Arm A vs B vs C vs D under drift and recovery."""

import json
import random
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python" / "microloop"))

from microloop import DecisionSite, FallbackResult, Microloop, Outcome, PromotionRequirements
from microloop.internal.contracts import canonical


def run_adaptation_benchmark():
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_file = results_dir / "adaptation_benchmark.json"

    site = DecisionSite("policy.dispatch", {"text": "string"}, ("allow", "deny"))

    arms = {
        "Arm_A_Static": {
            "allow_adaptive_comparison": False,
            "allow_region_split": False,
            "allow_auto_requalify": False,
            "run_maintenance_tighten": False,
        },
        "Arm_B_Adaptive_Comparison": {
            "allow_adaptive_comparison": True,
            "allow_region_split": False,
            "allow_auto_requalify": False,
            "run_maintenance_tighten": False,
        },
        "Arm_C_Adaptive_Margins": {
            "allow_adaptive_comparison": False,
            "allow_region_split": False,
            "allow_auto_requalify": False,
            "run_maintenance_tighten": True,
        },
        "Arm_D_Full_Self_Tuning": {
            "allow_adaptive_comparison": True,
            "allow_region_split": True,
            "allow_auto_requalify": True,
            "run_maintenance_tighten": True,
        },
    }

    def ground_truth(text, phase):
        # Base policy: "allow" for access/view/read, "deny" for write/delete/admin
        is_read = any(w in text for w in ("access", "view", "read"))
        if phase == 1:  # Stable steady state
            return "allow" if is_read else "deny"
        elif phase == 2:  # Gradual drift (20% random inversion)
            base = "allow" if is_read else "deny"
            return ("deny" if base == "allow" else "allow") if random.random() < 0.20 else base
        elif phase == 3:  # Localized subgroup drift: "access view" shifted to deny
            if "access view" in text:
                return "deny"
            return "allow" if is_read else "deny"
        elif phase == 4:  # Sudden global policy shift: complete inversion
            return "deny" if is_read else "allow"
        elif phase == 5:  # Recovery period: new inverted policy stabilized
            return "deny" if is_read else "allow"
        return "allow" if is_read else "deny"

    queries_pool = [
        "access read",
        "access view",
        "delete write",
        "delete drop",
    ]

    benchmark_results = {}

    for arm_name, config in arms.items():
        random.seed(42)
        req = PromotionRequirements(
            min_samples=5,
            min_quality=0.5,
            min_confidence=0.5,
            max_degradation=1.0,
            comparison_rate=0.25,
            min_region_samples=5,
            evaluation_window=100,
            min_comparison_rate=0.05,
            allow_adaptive_comparison=config["allow_adaptive_comparison"],
            allow_region_split=config["allow_region_split"],
            allow_auto_requalify=config["allow_auto_requalify"],
        )

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / f"{arm_name}.db"
            with Microloop(db_path) as client:
                def verify_p1(state, choice):
                    exp = ground_truth(state["text"], 1)
                    return Outcome(float(choice == exp), "bench_v1", "1", {"expected": exp})

                # Bootstrap qualification
                for i in range(240):
                    q = queries_pool[i % len(queries_pool)]
                    st = {"text": q}
                    exp = ground_truth(q, 1)
                    res = client.decide(site=site, state=st, fallback=lambda exp=exp: exp)
                    client.record_outcome(res.decision_id, **asdict(verify_p1(st, res.choice)))

                client.compile(site, engine="exact")
                client.calibrate(site, verifier=verify_p1, requirements=req)
                for i in range(120):
                    q = queries_pool[i % len(queries_pool)]
                    st = {"text": q}
                    exp = ground_truth(q, 1)
                    res = client.decide(site=site, state=st, fallback=lambda exp=exp: exp)
                    client.record_outcome(res.decision_id, **asdict(verify_p1(st, res.choice)))
                client.evaluate(site, verifier=verify_p1)

                total_served = 0
                fast_served = 0
                false_serves = 0
                teacher_calls = 0
                decisions_to_demote = None
                decisions_to_recover = None

                # Run through the 5 phases
                phases = [
                    (1, 300),  # Steady
                    (2, 300),  # Gradual drift
                    (3, 200),  # Subgroup drift
                    (4, 200),  # Sudden shift
                    (5, 400),  # Recovery
                ]

                global_decision_idx = 0
                demoted_at = None

                for phase_id, phase_count in phases:
                    for _ in range(phase_count):
                        global_decision_idx += 1
                        q = random.choice(queries_pool)
                        st = {"text": q}
                        expected = ground_truth(q, phase_id)

                        def fallback_fn(exp=expected):
                            return FallbackResult(exp, model_calls=1)

                        res = client.decide(site=site, state=st, fallback=fallback_fn)
                        total_served += 1

                        is_correct = (res.choice == expected)
                        if res.source == "fast_path":
                            fast_served += 1
                            if not is_correct:
                                false_serves += 1
                        else:
                            teacher_calls += 1

                        out = Outcome(
                            float(is_correct), "bench_verifier", "1", {"phase": phase_id}
                        )
                        client.record_outcome(res.decision_id, **asdict(out))

                        # Periodic host maintenance cycle
                        if global_decision_idx % 50 == 0:
                            if config["run_maintenance_tighten"]:
                                cur_pid = phase_id

                                def maint_ver(s, c, pid=cur_pid):
                                    corr = float(c == ground_truth(s["text"], pid))
                                    return Outcome(corr, "v", "1", {"e": True})

                                client.maintenance(
                                    verifier=maint_ver,
                                    requirements=req,
                                )
                            else:
                                client.reevaluate(site)

                            art_now = client._artifact(site.version)
                            if (
                                phase_id in (2, 3, 4)
                                and art_now
                                and art_now["status"] == "SHADOW"
                                and decisions_to_demote is None
                            ):
                                decisions_to_demote = global_decision_idx - 300
                                demoted_at = global_decision_idx

                            if (
                                phase_id == 5
                                and art_now
                                and art_now["status"] == "ACTIVE"
                                and demoted_at
                                and decisions_to_recover is None
                            ):
                                decisions_to_recover = global_decision_idx - demoted_at

                accuracy_served = round(1.0 - (false_serves / max(1, fast_served)), 4)
                benchmark_results[arm_name] = {
                    "total_decisions": total_served,
                    "fast_path_served": fast_served,
                    "fast_path_ratio": round(fast_served / total_served, 3),
                    "teacher_fallback_calls": teacher_calls,
                    "false_serves": false_serves,
                    "false_serve_rate": round(false_serves / max(1, fast_served), 4),
                    "accuracy_on_served": accuracy_served,
                    "decisions_to_demote": decisions_to_demote or "N/A",
                    "decisions_to_recover": decisions_to_recover or "N/A",
                    "net_calls_avoided": fast_served,
                }

    out_file.write_text(canonical(benchmark_results), encoding="utf-8")
    print(f"Adaptation benchmark complete. Saved to {out_file}")
    print(json.dumps(benchmark_results, indent=2))


if __name__ == "__main__":
    run_adaptation_benchmark()
