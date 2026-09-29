"""Decision-oriented CLI, with legacy commands kept available."""

from __future__ import annotations

import argparse
import importlib
import json
import sqlite3
from pathlib import Path

from .decision_api import Microloop
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
            "compile",
            "evaluate",
            "maintenance",
            "export",
            "retain",
            "model-install",
            "model-train",
        ],
    )
    parser.add_argument("site", nargs="?")
    parser.add_argument("--db", default=".microloop/decisions.db")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--checkpoint")
    parser.add_argument("--replace", action="store_true", help="Retire the current candidate")
    parser.add_argument("--verifier", help="Explicit trusted Python module:callable")
    parser.add_argument("--requirements", help="JSON file with experiment requirements")
    parser.add_argument("--before", type=float, help="Retention cutoff as Unix timestamp")
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
            from .internal.model.training import finetune

            if not args.data or not args.output:
                raise ValueError("model-train requires --data JSONL and --output directory")
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
        if args.command in {"sites", "inspect"} and not Path(args.db).is_file():
            if args.command == "sites":
                print("[]" if args.json else "No decision sites recorded.")
                return 0
            raise ValueError("Decision database does not exist")
        with Microloop(
            args.db,
            engines=[DecisionModelEngine(args.checkpoint)],
            readonly=args.command in {"sites", "inspect", "export"},
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
                result = client.inspect(args.site)
            elif args.command == "compile":
                result = {
                    "artifact": client.compile(
                        args.site, engine="decision", replace_existing=args.replace
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
                    verifier=verifier, requirements=requirements, engine="decision"
                )
            elif args.command == "export":
                if not args.site:
                    raise ValueError("Export requires an output path")
                client.store.export(args.site)
                result = {"exported": args.site}
            else:
                if args.before is None:
                    raise ValueError("Retention requires --before timestamp")
                if args.site:
                    site = client._resolve(args.site)
                    result = {"deleted": client.store.retain_site(site.version, args.before)}
                else:
                    result = {"deleted": client.store.retain_since(args.before)}
            if args.json or args.command not in {"sites", "inspect"}:
                print(json.dumps(result, indent=2))
            else:
                for row in result if isinstance(result, list) else [result]:
                    print(
                        f"{row['name']}  {row['state']}\n"
                        f"  observations  {row['observations']}\n"
                        f"  coverage      {row['coverage']:.1%}\n"
                        f"  fallback      {row['fallbacks']}\n"
                        f"  outcome delta {row['outcome_delta']}\n"
                        f"  fast path     {row['fast_path'] or 'none'}"
                    )
        return 0
    except (ValueError, KeyError, OSError, ImportError, sqlite3.Error) as error:
        parser.exit(2, f"microloop: {error}\n")
