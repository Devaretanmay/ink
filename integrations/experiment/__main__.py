"""Run the static-vs-adaptive experiment.

Offline (default), using the deterministic scripted provider::

    python -m integrations.experiment

Against Anthropic, using real model calls (requires ``anthropic`` and an API
key). This is the real experiment and takes real time and money::

    python -m integrations.experiment --real --tasks 32

Real runs map the tier ladder onto concrete model ids from the environment
(``MICROLOOP_MODEL_FAST`` / ``MICROLOOP_MODEL_BALANCED`` / ``MICROLOOP_MODEL_STRONG``),
so no model name is ever hardcoded. Simulated results are labelled as such and
never mixed with real ones.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from microloop import ModelTier
from microloop.store import EpisodeStore

from integrations.coding_harness.providers import AnthropicProvider
from integrations.coding_harness.tasks import build_tasks
from integrations.experiment.runner import build_session, run_experiment, run_sweep

_ENV_TIER = {
    ModelTier.Fast: "MICROLOOP_MODEL_FAST",
    ModelTier.Balanced: "MICROLOOP_MODEL_BALANCED",
    ModelTier.Strong: "MICROLOOP_MODEL_STRONG",
}


def _real_tiers() -> dict[str, str]:
    tiers: dict[str, str] = {}
    for tier, variable in _ENV_TIER.items():
        model = os.environ.get(variable)
        if not model:
            raise SystemExit(
                f"--real needs {variable} set (a concrete model id for the "
                f"'{tier}' tier); Microloop never hardcodes model names"
            )
        tiers[tier] = model
    return tiers


def _simulated_tiers() -> dict[str, str]:
    from integrations.experiment.runner import DEFAULT_TIERS

    return dict(DEFAULT_TIERS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="experiment", description=__doc__)
    parser.add_argument("--tasks", type=int, default=32, help="how many tasks to run")
    parser.add_argument(
        "--real",
        action="store_true",
        help="use Anthropic model calls instead of the offline agent model",
    )
    parser.add_argument(
        "--db",
        default=".microloop/experiments.db",
        help="episode database path (default .microloop/experiments.db)",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--max-steps", type=int, default=40)
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="compare controller policies against each other and against no adaptation",
    )
    args = parser.parse_args(argv)

    tasks = build_tasks(args.tasks)
    if args.real:
        tiers = _real_tiers()

        def arm_builder(arm: str, **kwargs: object):  # noqa: ANN202
            return build_session(arm, tiers=tiers)

        provider_name, run_mode = "anthropic", "real"

        def provider_factory(task):  # noqa: ANN001, ANN202
            return AnthropicProvider()

    else:
        from integrations.experiment.runner import _simulated_factory, _tier_by_model

        tiers = _simulated_tiers()

        def arm_builder(arm: str, **kwargs: Any):  # noqa: ANN202
            return build_session(arm, tiers=tiers, policy=kwargs.get("policy"))

        provider_name, run_mode = "simulated-agent", "simulated"
        provider_factory = _simulated_factory(_tier_by_model(tiers))

    store = EpisodeStore(args.db)
    try:
        runner = run_sweep if args.sweep else run_experiment
        report = runner(
            tasks,
            store=store,
            run_mode=run_mode,
            provider=provider_name,
            provider_factory=provider_factory,
            arm_builder=arm_builder,
            tiers=tiers,
            max_steps=args.max_steps,
        )
    finally:
        store.close()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.render())
        if run_mode == "simulated":
            print()
            print(
                "run mode is simulated: this measures the controller against a "
                "model of an agent, not against a real model."
            )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
