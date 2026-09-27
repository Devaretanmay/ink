"""Run the static-vs-adaptive experiment.

Offline (default), against the deterministic agent model::

    python -m integrations.experiment

Against a real model over an OpenAI-compatible API (Groq by default), using real
tool calls and real time and tokens::

    python -m integrations.experiment --real --provider groq --tasks 1

Against Anthropic, which needs the ``anthropic`` package installed::

    python -m integrations.experiment --real --provider anthropic --tasks 1

Real runs map the tier ladder onto concrete model ids from the environment
(``MICROLOOP_MODEL_FAST`` / ``MICROLOOP_MODEL_BALANCED`` / ``MICROLOOP_MODEL_STRONG``),
so no model name is ever hardcoded. A single-model experiment sets all three to
the same id, which is the fair way to isolate the controller: if every tier is
the same model, escalation cannot help, and whatever the sweep shows is the
controller and not a better model. Simulated results are labelled as such and
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

from integrations.coding_harness.providers import AnthropicProvider, GroqProvider
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


def _single_model_tiers(model: str) -> dict[str, str]:
    """Pin every tier to one model id.

    This is the honest way to ask whether the controller helps: with one model on
    every rung, a model switch is a no-op, so a difference between the arms is
    attributable to the controller's other actions rather than to a better model.
    """
    return {tier: model for tier in ModelTier.Order}


def _simulated_tiers() -> dict[str, str]:
    from integrations.experiment.runner import DEFAULT_TIERS

    return dict(DEFAULT_TIERS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="experiment", description=__doc__)
    parser.add_argument("--tasks", type=int, default=32, help="how many tasks to run")
    parser.add_argument(
        "--task-set",
        choices=("generated", "real"),
        default="generated",
        help=(
            "generated: one-line arithmetic, solved in one attempt by a capable "
            "model; real: Python bugs with a hidden verifier, where the obvious "
            "first fix can be wrong"
        ),
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="make real model calls instead of running the offline agent model",
    )
    parser.add_argument(
        "--provider",
        choices=("groq", "anthropic"),
        default="groq",
        help="which real API to call with --real (default groq)",
    )
    parser.add_argument(
        "--model",
        help=(
            "a single model id pinned to every tier, which isolates the "
            "controller from model switching; overrides the MICROLOOP_MODEL_* "
            "variables"
        ),
    )
    parser.add_argument(
        "--max-calls",
        type=int,
        default=None,
        help="hard ceiling on real API calls, so a metered run cannot overshoot",
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

    if args.task_set == "real":
        from integrations.coding_harness.real_tasks import build_real_tasks

        tasks = build_real_tasks()[: args.tasks]
    else:
        tasks = build_tasks(args.tasks)
    if args.real:
        tiers = _single_model_tiers(args.model) if args.model else _real_tiers()

        def arm_builder(arm: str, **kwargs: Any):  # noqa: ANN202
            return build_session(arm, tiers=tiers, policy=kwargs.get("policy"))

        if args.provider == "anthropic":
            provider_name, run_mode = "anthropic", "real"

            def provider_factory(task):  # noqa: ANN001, ANN202
                return AnthropicProvider()

        else:
            provider_name, run_mode = "groq", "real"
            budget = args.max_calls

            def provider_factory(task):  # noqa: ANN001, ANN202
                # A fresh budget per run, divided by the number of runs, so the
                # ceiling is the caller's and the split is ours.
                return GroqProvider(max_calls=budget)

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
