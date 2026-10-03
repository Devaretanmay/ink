"""Microloop CLI command implementations."""

from __future__ import annotations

import argparse
import importlib
import json
import sqlite3
import time
from pathlib import Path

from .decision_api import Microloop
from .discovery import discover_from_file
from .internal.contracts import PromotionRequirements
from .internal.engines import DecisionModelEngine


def load_callable(spec):
    module, name = spec.split(":", 1)
    value = getattr(importlib.import_module(module), name)
    if not callable(value):
        raise ValueError("Verifier must be callable")
    return value


def main(argv):
    parser = argparse.ArgumentParser(prog="microloop")
    parser.add_argument(
        "command",
        choices=[
            "sites",
            "inspect",
            "status",
            "compile",
            "evaluate",
            "maintenance",
            "export",
            "retain",
            "model-install",
            "model-train",
            "discover",
            "value",
        ],
    )
    parser.add_argument("site", nargs="?")
    parser.add_argument("--db", default=".microloop/decisions.db")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--profile", action="store_true", help="Include deep economic profile in discover"
    )
    parser.add_argument(
        "--snippet", action="store_true", help="Print integration code snippet for discovered sites"
    )
    parser.add_argument("--checkpoint")
    parser.add_argument("--replace", action="store_true", help="Retire the current candidate")
    parser.add_argument("--verifier", help="Explicit trusted Python module:callable")
    parser.add_argument("--engine", default="decision", help="Engine to compile: decision or exact")
    parser.add_argument("--requirements", help="JSON file with experiment requirements")
    parser.add_argument("--before", type=float, help="Retention cutoff as Unix timestamp")
    parser.add_argument("--days", type=float, help="Retention cutoff in days")
    parser.add_argument("--data", help="JSONL rows for model-train: state/choices/choice")
    parser.add_argument("--output", help="Output directory for model-train checkpoint")
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    try:
        if args.command == "model-install":
            from .internal.model.registry import install

            print(
                json.dumps(
                    {"model": "microloop-decision-v1", "path": str(install(args.checkpoint))}
                )
            )
            return 0
        if args.command == "model-train":
            if not args.data or not args.output:
                raise ValueError("model-train requires --data JSONL and --output directory")
            from .internal.model.training import finetune

            rows = [
                json.loads(line)
                for line in Path(args.data).read_text().splitlines()
                if line.strip()
            ]
            card = finetune(
                args.checkpoint, rows, args.output, steps=args.steps, lr=args.lr, seed=args.seed
            )
            print(json.dumps(card, indent=2))
            return 0
        if args.command == "discover":
            if not args.site:
                raise ValueError("discover requires a trace file path (JSON or JSONL)")
            candidates = discover_from_file(args.site)
            if args.json:
                print(json.dumps([c.to_dict() for c in candidates], indent=2))
                return 0
            print(f"Found {len(candidates)} candidate call sites.\n")
            for i, c in enumerate(candidates, 1):
                rec_label = (
                    "STRONG CANDIDATE"
                    if c.recommendation == "compile"
                    else c.recommendation.upper()
                )
                print(f"{i}. {c.site_name}")
                print(f"   traffic: {c.call_frequency:,.0f}/day ({c.total_calls} observed)")
                print(
                    f"   repetition: {c.repetition_rate:.1%}"
                    + (
                        f" (templated: {c.templated_repetition_rate:.1%})"
                        if c.templated_repetition_rate > c.repetition_rate
                        else ""
                    )
                )
                print(f"   choices: {len(c.choices)} {c.choices[:5]}")
                vr = c.verifier_readiness.upper()
                print(f"   verifier readiness: {vr} ({c.verifier_coverage:.1%} coverage)")
                print(f"   model latency: {c.p50_latency_ms:.1f}ms")
                print(f"   estimated break-even: {c.break_even_decisions} decisions")
                print(f"   estimated annual savings: ${c.estimated_annual_savings:,.2f}")
                print(f"   recommendation: {rec_label}")
                print(f"   reason: {c.reason}")
                if c.volatile_fields:
                    print(
                        f"   volatile fields: {c.volatile_fields}\n"
                        "     (suggested exclusions - developer review required)"
                    )
                if args.profile:
                    print(
                        f"   [PROFILE] entropy: {c.output_entropy:.2f} bits | "
                        f"stability: {c.categorical_stability:.1%}"
                    )
                    print(
                        f"   [PROFILE] qual cost: ${c.qualification_cost_usd:.4f} | "
                        f"avg cost: ${c.avg_cost:.6f}"
                    )
                if args.snippet and c.snippet:
                    print("   [INTEGRATION SNIPPET]:")
                    for sline in c.snippet.splitlines():
                        print(f"     {sline}")
                print()
            return 0
        if args.command == "value":
            if not Path(args.db).is_file():
                if args.json:
                    print(
                        json.dumps(
                            {"error": "Decision database does not exist", "database": args.db}
                        )
                    )
                else:
                    print(f"No decision database found at {args.db}.")
                return 0
            with Microloop(args.db, readonly=True) as client:
                sites = client.sites()
                compiled_count = sum(1 for s in sites if s.get("fast_path") is not None)
                active_count = sum(1 for s in sites if s.get("state") == "ACTIVE")
                avoided_total = sum(s.get("fallbacks_avoided", 0) for s in sites)
                fallback_total = sum(s.get("fallbacks", 0) for s in sites)
                comparison_total = sum(
                    s.get("profiler", {}).get("reasons", {}).get("comparison", 0) for s in sites
                )
                cost_saved = sum(
                    s.get("fallbacks_avoided", 0)
                    * s.get("profiler", {}).get("fallback_cost_per_decision", 0.002)
                    for s in sites
                )
                past_break_even = sum(
                    1
                    for s in sites
                    if s.get("fallbacks_avoided", 0)
                    >= s.get("profiler", {}).get("break_even_decisions", 999999)
                )
                val_data = {
                    "database": args.db,
                    "registered_sites": len(sites),
                    "compiled_sites": compiled_count,
                    "active_sites": active_count,
                    "model_calls_avoided": avoided_total,
                    "estimated_cost_saved_usd": round(cost_saved, 4),
                    "fallback_calls": fallback_total,
                    "comparison_calls": comparison_total,
                    "sites_past_break_even": f"{past_break_even}/{len(sites)}",
                }
                if args.json:
                    print(json.dumps(val_data, indent=2))
                else:
                    print("=" * 45)
                    print("MICROLOOP VALUE REPORT")
                    print("=" * 45)
                    print(f"Database              : {args.db}")
                    print(f"Registered sites      : {len(sites)}")
                    print(f"Compiled sites        : {compiled_count}")
                    print(f"Active sites          : {active_count}")
                    print(f"Model calls avoided   : {avoided_total:,}")
                    print(f"Estimated cost saved  : ${cost_saved:,.4f}")
                    print(f"Fallback calls        : {fallback_total:,}")
                    print(f"Comparison calls      : {comparison_total:,}")
                    print(f"Sites past break-even : {past_break_even}/{len(sites)}")
                    print("=" * 45)
            return 0
        if args.command in {"sites", "inspect", "status"} and not Path(args.db).is_file():
            if args.command == "sites":
                print("[]" if args.json else "No decision sites recorded.")
                return 0
            raise ValueError("Decision database does not exist")
        with Microloop(
            args.db,
            engines=[DecisionModelEngine(args.checkpoint)],
            readonly=args.command in {"sites", "inspect", "status", "export"},
        ) as client:
            requirements = (
                PromotionRequirements(**json.loads(Path(args.requirements).read_text()))
                if args.requirements
                else None
            )
            verifier = load_callable(args.verifier) if args.verifier else None
            if args.command == "sites":
                result = client.sites()
            elif args.command == "inspect":
                if not args.site:
                    raise ValueError(
                        "inspect requires a site name (e.g. 'microloop inspect <site>')"
                    )
                result = client.inspect(args.site)
            elif args.command == "status":
                if args.site:
                    result = client.status(args.site)
                else:
                    sites = client.sites()
                    result = [client.status(s["name"]) for s in sites]
            elif args.command == "compile":
                result = {
                    "artifact": client.compile(
                        args.site, engine=args.engine, replace_existing=args.replace
                    )
                }
            elif args.command == "evaluate":
                if verifier is None:
                    raise ValueError("Evaluation requires --verifier module:callable")
                site = client._resolve(args.site)
                artifact = client._artifact(site.version)
                if artifact and artifact["profile"] is None:
                    if requirements is None:
                        raise ValueError("First evaluation requires --requirements")
                    client.calibrate(site, verifier=verifier, requirements=requirements)
                result = client.evaluate(site, verifier=verifier)
            elif args.command == "maintenance":
                result = client.maintenance(
                    verifier=verifier, requirements=requirements, engine=args.engine
                )
            elif args.command == "export":
                if not args.site:
                    raise ValueError("Export requires an output path")
                client.store.export(args.site)
                result = {"exported": args.site}
            elif args.command == "retain":
                before = args.before
                if args.days is not None:
                    before = time.time() - (args.days * 86400.0)
                if args.site:
                    result = client.compact(args.site, before_timestamp=before, vacuum=True)
                else:
                    if before is None:
                        raise ValueError("Retention requires --before timestamp or --days cutoff")
                    result = client.compact(None, before_timestamp=before, vacuum=True)
            if args.json or args.command not in {"sites", "inspect", "status"}:
                print(json.dumps(result, indent=2))
            elif args.command == "status":
                rows = result if isinstance(result, list) else [result]
                if not rows:
                    print("No decision sites recorded.")
                for row in rows:
                    print(
                        f"{row['name']}  {row['state']}\n"
                        f"  blocker       {row.get('blocker', 'none')}\n"
                        f"  observations  {row['observations']}\n"
                        f"  outcomes      {row['outcomes']} ({row['outcome_coverage']:.1%})\n"
                        f"  fast served   {row['fast_served']}\n"
                        f"  fallback      {row['fallbacks']}\n"
                        f"  fast path     {row['active_revision'] or 'none'}"
                    )
                    if len(rows) > 1:
                        print()
            else:
                for row in result if isinstance(result, list) else [result]:
                    print(
                        f"{row['name']}  {row['state']}\n"
                        f"  blocker       {row.get('blocker', 'none')}\n"
                        f"  observations  {row['observations']}\n"
                        f"  coverage      {row['coverage']:.1%}\n"
                        f"  fallback      {row['fallbacks']}\n"
                        f"  outcome delta {row['outcome_delta']}\n"
                        f"  fast path     {row['fast_path'] or 'none'}"
                    )
        return 0
    except (ValueError, KeyError, OSError, ImportError, sqlite3.Error) as error:
        parser.exit(2, f"microloop: {error}\n")
