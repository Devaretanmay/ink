"""Run the first real experiment: does adaptive execution actually help?

Every task runs twice -- once with a static runtime, once with Microloop
adapting -- on the same task, the same starting workspace, the same provider and
the same step budget. The comparison is task success, cost per success, tokens
per success and steps.

A scripted provider means the whole experiment runs offline and deterministically
(``run_mode="simulated"``). Swapping in a real provider produces
``run_mode="real"`` runs; the runner does not care which.

Microloop must be allowed to lose. If the adaptive arm does worse, the report
says so; that is the finding, not a bug to tune away.
"""
from __future__ import annotations

import math
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from microloop import (
    Capabilities,
    ContextCompactor,
    ModelTier,
    Monitor,
    RuntimeSession,
    ScoredController,
    TieredAdapter,
)
from microloop.store import EpisodeStore

from integrations.coding_harness.harness import CodingHarness, RunResult, Task
from integrations.coding_harness.providers import CallBudget, Provider, SimulatedCodingProvider

__all__ = [
    "ArmSummary",
    "ControllerPolicy",
    "ExperimentReport",
    "POLICIES",
    "SweepReport",
    "SweepRow",
    "build_session",
    "run_experiment",
    "run_sweep",
]


#: Provider-neutral model ladder for the harness. A real provider maps these
#: onto whatever model ids it currently sells.
DEFAULT_TIERS = {
    ModelTier.Fast: "harness-fast",
    ModelTier.Balanced: "harness-balanced",
    ModelTier.Strong: "harness-strong",
}


@dataclass
class ControllerPolicy:
    """One named controller configuration to compare against another.

    A policy is not a tuning knob to be searched until the adaptive arm wins. It
    is a hypothesis under test, and the sweep reports all of them, including the
    ones that lose.
    """

    name: str
    cooldown_steps: int = 1
    min_benefit: float = 0.15
    max_interventions: int = 1_000

    def note(self) -> str:
        return f"cooldown {self.cooldown_steps}, benefit {self.min_benefit}"


#: The policies the sweep compares. ``eager`` is the configuration the controller
#: shipped with; the other two exist because the eager result turned out to be
#: harmful and the question became *how* harmful.
POLICIES: tuple[ControllerPolicy, ...] = (
    ControllerPolicy("eager", cooldown_steps=1, min_benefit=0.15, max_interventions=1_000),
    ControllerPolicy("patient", cooldown_steps=6, min_benefit=0.15, max_interventions=1_000),
    ControllerPolicy("wary", cooldown_steps=10, min_benefit=0.30, max_interventions=3),
)


def _tier_by_model(tiers: dict[str, str]) -> dict[str, str]:
    """Invert the tier map so a provider can recognise which rung it is on."""
    return {model: tier for tier, model in tiers.items()}


def build_session(
    arm: str,
    *,
    tiers: dict[str, str] | None = None,
    policy: ControllerPolicy | None = None,
) -> RuntimeSession:
    """Build the runtime session for one arm.

    ``static`` observes with the default monitor: it reports progress but never
    adapts. ``adaptive`` scores candidates and actuates them. Both arms start on
    the same tier with the same adapter, so the only difference between them is
    whether the controller acts.

    ``tiers`` lets a real provider map the same ladder onto its own model ids.
    """
    adapter = TieredAdapter(
        tiers or DEFAULT_TIERS,
        start=ModelTier.Fast,
        context_limit=64_000,
        compactor=ContextCompactor(),
        capabilities=Capabilities(
            replan=True, model_switch=True, context_compaction=True
        ),
    )
    if arm == "static":
        return RuntimeSession(Monitor(), adapter)
    if arm != "adaptive":
        raise ValueError(f"unknown arm {arm!r}; expected 'static' or 'adaptive'")
    chosen = policy or POLICIES[0]
    controller = ScoredController(
        stalled="replan",
        regressing="stop",
        cooldown_steps=chosen.cooldown_steps,
        max_interventions=chosen.max_interventions,
        min_benefit=chosen.min_benefit,
        capabilities=adapter.capabilities(),
    )
    return RuntimeSession(Monitor(controller=controller), adapter)


def _simulated_factory(tier_by_model: dict[str, str]) -> Callable[[Task], Provider]:
    """A provider factory for the offline agent model."""

    def make(task: Task) -> Provider:
        return SimulatedCodingProvider(
            filename=task.filename,
            wrong_source=task.wrong_source,
            correct_source=task.correct_source,
            behaviour=task.behaviour,
            tier_by_model=tier_by_model,
        )

    return make


@dataclass
class ArmSummary:
    """Aggregate result for one arm."""

    arm: str
    episodes: int = 0
    successes: int = 0
    cost: float = 0.0
    tokens: int = 0
    steps: int = 0
    adaptations: int = 0
    wall_seconds: float = 0.0
    #: Per-task success, kept so policies can be compared pairwise. Aggregation
    #: throws away the pairing, and the pairing is what makes the comparison
    #: meaningful: both arms run the same tasks.
    outcomes: dict[str, bool] = field(default_factory=dict, repr=False)

    @property
    def success_rate(self) -> float:
        return self.successes / self.episodes if self.episodes else 0.0

    @property
    def cost_per_success(self) -> float | None:
        return self.cost / self.successes if self.successes else None

    @property
    def tokens_per_success(self) -> float | None:
        return self.tokens / self.successes if self.successes else None

    @property
    def steps_per_success(self) -> float | None:
        return self.steps / self.successes if self.successes else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "episodes": self.episodes,
            "successes": self.successes,
            "success_rate": round(self.success_rate, 4),
            "cost": round(self.cost, 4),
            "tokens": self.tokens,
            "steps": self.steps,
            "adaptations": self.adaptations,
            "wall_seconds": round(self.wall_seconds, 4),
            "cost_per_success": (
                None if self.cost_per_success is None else round(self.cost_per_success, 4)
            ),
            "tokens_per_success": (
                None
                if self.tokens_per_success is None
                else round(self.tokens_per_success, 2)
            ),
            "steps_per_success": (
                None if self.steps_per_success is None else round(self.steps_per_success, 2)
            ),
        }


@dataclass
class ExperimentReport:
    """The whole experiment, per arm."""

    run_mode: str
    provider: str
    tasks: int
    arms: dict[str, ArmSummary] = field(default_factory=dict)
    #: Tasks that were not run because the call budget could not cover them.
    #: Reported rather than silently dropped, because a short run and a failed
    #: run are different facts.
    skipped: int = 0

    def render(self) -> str:
        lines = [
            "Microloop experiment",
            f"provider   {self.provider}",
            f"run mode   {self.run_mode}",
            f"tasks      {self.tasks}",
            "",
            f"{'arm':<12}{'success':>10}{'cost/succ':>12}{'tok/succ':>12}{'step/succ':>12}",
        ]
        for name in ("static", "adaptive"):
            summary = self.arms.get(name)
            if summary is None:
                continue
            rate = f"{summary.successes}/{summary.episodes}"
            cost = summary.cost_per_success
            tokens = summary.tokens_per_success
            steps = summary.steps_per_success
            lines.append(
                f"{name:<12}{rate:>10}"
                f"{(f'${cost:.2f}' if cost is not None else 'n/a'):>12}"
                f"{(f'{tokens:.0f}' if tokens is not None else 'n/a'):>12}"
                f"{(f'{steps:.1f}' if steps is not None else 'n/a'):>12}"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_mode": self.run_mode,
            "provider": self.provider,
            "tasks": self.tasks,
            "skipped": self.skipped,
            "arms": {name: summary.to_dict() for name, summary in self.arms.items()},
        }


def run_experiment(
    tasks: Iterable[Task],
    *,
    arms: tuple[str, ...] = ("static", "adaptive"),
    store: EpisodeStore | None = None,
    run_mode: str = "simulated",
    provider: str = "scripted",
    provider_factory: Callable[[Task], Provider] | None = None,
    arm_builder: Callable[[str], RuntimeSession] = build_session,
    tiers: dict[str, str] | None = None,
    policy: ControllerPolicy | None = None,
    arm_label: str | None = None,
    max_steps: int = 40,
    call_budget: CallBudget | None = None,
) -> ExperimentReport:
    """Run every task under every arm and aggregate the results.

    The provider is rebuilt for every run, so the two arms face an agent in
    exactly the same starting state. Nothing about a task's behaviour depends on
    which arm is running it.
    """
    ladder = tiers or DEFAULT_TIERS
    factory = provider_factory or _simulated_factory(_tier_by_model(ladder))
    task_list = list(tasks)
    report = ExperimentReport(run_mode=run_mode, provider=provider, tasks=len(task_list))
    for name in arms:
        report.arms[name] = ArmSummary(arm=name)

    # Reserve the worst case for a whole run before starting one. Without this a
    # metered run launches every remaining task, each of which discovers the
    # budget is gone on its first turn and reports budget_exhausted, and a run
    # that ran out of money ends up looking like a run where every task failed.
    for task in task_list:
        if call_budget is not None and not call_budget.can_afford(max_steps):
            report.skipped = len(task_list) - len(report.arms["static"].outcomes)
            break
        for name in arms:
            session = arm_builder(name, policy=policy)
            harness = CodingHarness(
                task=task,
                provider=factory(task),
                session=session,
                arm=name,
                max_steps=max_steps,
                workspace=Path(tempfile.mkdtemp()),
            )
            started = time.perf_counter()
            result = harness.run()
            elapsed = time.perf_counter() - started
            _fold(report.arms[name], result, elapsed)
            if store is not None:
                # Record under the arm's own label so a sweep does not collapse
                # every policy into one indistinguishable row in the store.
                label = arm_label or name
                store.record(
                    result.episode,
                    run_id=f"{label}:{task.name}",
                    task=task.name,
                    arm=label,
                    run_mode=run_mode,
                    success=result.success,
                    verifier="harness-tests",
                    score=1.0 if result.success else 0.0,
                )
    return report


def _fold(summary: ArmSummary, result: RunResult, elapsed: float) -> None:
    summary.episodes += 1
    summary.successes += 1 if result.success else 0
    summary.cost += result.cost
    summary.tokens += result.input_tokens + result.output_tokens
    summary.steps += result.steps
    summary.adaptations += result.adaptations
    summary.wall_seconds += elapsed
    summary.outcomes[result.task] = result.success


# -- policy sweep -------------------------------------------------------------


def _sign_test(won: int, lost: int) -> float:
    """Two-sided exact sign test on the tasks where two arms disagreed.

    The smallest p this can return with a usable reading is 0.25, because two
    discordant tasks is the fewest that carries any information at all. That
    floor is the point: it stops a one-task flip from being reported as a
    result.
    """
    n = won + lost
    if n == 0:
        return 1.0
    smaller = min(won, lost)
    tail = sum(math.comb(n, k) for k in range(smaller + 1)) / (2**n)
    return min(1.0, 2 * tail)


def _resolution(verdict: dict[str, Any]) -> int:
    """The fewest discordant tasks this experiment could have detected.

    With ``d`` tasks where two arms disagreed, the smallest p a sign test can
    reach is ``2 ** (1 - d)``. That crosses 0.05 at ``d = 6``, so a sweep with
    fewer than six discordant pairs cannot reach significance at all, no matter
    how large the swing looks in the totals.
    """
    return max(
        (row["discordant"] for row in verdict.get("comparisons", [])),
        default=0,
    )


#: Discordant tasks needed before a sign test can reach p < 0.05 at all.
_SIGN_TEST_FLOOR = 6


@dataclass
class SweepRow:
    """One row of the controller comparison."""

    name: str
    summary: ArmSummary
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "detail": self.detail, **self.summary.to_dict()}


@dataclass
class SweepReport:
    """Static baseline plus one adaptive row per controller policy."""

    run_mode: str
    tasks: int
    rows: list[SweepRow] = field(default_factory=list)

    def verdict(self) -> dict[str, Any]:
        """Compare each policy to the static baseline, and say how sure we are.

        Wins and losses are counted per task as a paired comparison, because the
        two arms run the same tasks and the pairing is what removes the variance
        of task difficulty. A sign test on those pairs is the weakest test that
        still respects the pairing, and it is deliberately the one reported: a
        result that cannot clear it should not be described as a win.
        """
        if len(self.rows) < 2:
            return {"comparable": False, "reason": "no policy rows"}
        baseline = self.rows[0]
        comparisons = []
        for row in self.rows[1:]:
            policy = row.summary.outcomes
            won = [
                name
                for name, was in baseline.summary.outcomes.items()
                if policy.get(name) and not was
            ]
            lost = [
                name
                for name, was in baseline.summary.outcomes.items()
                if was and not policy.get(name)
            ]
            p_value = _sign_test(len(won), len(lost))
            comparisons.append(
                {
                    "policy": row.name,
                    "won": len(won),
                    "lost": len(lost),
                    "net": len(won) - len(lost),
                    "discordant": len(won) + len(lost),
                    "p_value": p_value,
                    "significant": p_value < 0.05,
                }
            )
        return {
            "comparable": True,
            "baseline": baseline.name,
            "baseline_successes": baseline.summary.successes,
            "comparisons": comparisons,
        }

    def render(self) -> str:
        """Render the sweep, and say plainly which side won."""
        lines = [
            "Microloop controller sweep",
            f"run mode   {self.run_mode}",
            f"tasks      {self.tasks}",
            "",
            f"{'policy':<14}{'success':>10}{'cost/succ':>12}{'tok/succ':>12}{'step/succ':>12}",
        ]
        for row in self.rows:
            summary = row.summary
            cost = summary.cost_per_success
            tokens = summary.tokens_per_success
            steps = summary.steps_per_success
            lines.append(
                f"{row.name:<14}{f'{summary.successes}/{summary.episodes}':>10}"
                f"{(f'${cost:.2f}' if cost is not None else 'n/a'):>12}"
                f"{(f'{tokens:.0f}' if tokens is not None else 'n/a'):>12}"
                f"{(f'{steps:.1f}' if steps is not None else 'n/a'):>12}"
            )
        lines.append("")
        lines.extend(self._conclusion())
        return "\n".join(lines)

    def _conclusion(self) -> list[str]:
        """State the result at the strength the evidence actually supports."""
        verdict = self.verdict()
        if not verdict.get("comparable"):
            return ["no comparison available"]
        out = ["Paired against doing nothing"]
        for row in verdict["comparisons"]:
            mark = "significant" if row["significant"] else "not significant"
            out.append(
                f"  {row['policy']:<10} won {row['won']}, lost {row['lost']}"
                f"  (p={row['p_value']:.3f}, {mark})"
            )
        best = max(
            self.rows[1:],
            key=lambda row: row.summary.successes,
            default=None,
        )
        if best is not None:
            out.append("")
            out.append(
                f"highest raw score: {best.name} at "
                f"{best.summary.successes}/{best.summary.episodes}"
            )
        significant = [r for r in verdict["comparisons"] if r["significant"]]
        harmful = [r for r in significant if r["net"] < 0]
        helpful = [r for r in significant if r["net"] > 0]
        out.append("")
        if helpful:
            out.append(
                f"conclusion: {helpful[0]['policy']} beats doing nothing on this "
                f"task set (p={helpful[0]['p_value']:.3f})."
            )
        elif harmful:
            out.append(
                f"conclusion: {harmful[0]['policy']} does WORSE than doing nothing "
                f"on this task set (p={harmful[0]['p_value']:.3f}). Adaptation as "
                f"configured is costing more than it returns."
            )
        else:
            discordant = _resolution(verdict)
            shortfall = max(0, _SIGN_TEST_FLOOR - discordant)
            detail = (
                f"With at most {discordant} task"
                f"{'' if discordant == 1 else 's'} where any policy disagreed "
                f"with doing nothing"
            )
            if shortfall:
                detail += (
                    f", and {shortfall} more would be needed before a sign test "
                    f"could reach p<0.05 at all"
                )
            out.append(
                f"conclusion: no policy differs from doing nothing at p<0.05. "
                f"{detail}. This experiment is underpowered rather than "
                f"conclusive: no benefit is demonstrated, and no harm is ruled "
                f"out. More tasks, or tasks where the outcome is actually "
                f"decided by intervention timing, are needed before any of "
                f"these rows means anything."
            )
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_mode": self.run_mode,
            "tasks": self.tasks,
            "rows": [row.to_dict() for row in self.rows],
            "verdict": self.verdict(),
        }


def run_sweep(
    tasks: Iterable[Task],
    *,
    policies: Iterable[ControllerPolicy] = POLICIES,
    store: EpisodeStore | None = None,
    run_mode: str = "simulated",
    provider: str = "simulated-agent",
    provider_factory: Callable[[Task], Provider] | None = None,
    arm_builder: Callable[..., RuntimeSession] = build_session,
    tiers: dict[str, str] | None = None,
    max_steps: int = 40,
    call_budget: CallBudget | None = None,
) -> SweepReport:
    """Compare controller policies against each other and against doing nothing.

    Every policy is reported, including the ones that lose. A sweep that only
    printed its best row would be a tuning loop wearing a lab coat.
    """
    task_list = list(tasks)
    report = SweepReport(run_mode=run_mode, tasks=len(task_list))

    static = run_experiment(
        task_list,
        arms=("static",),
        store=store,
        run_mode=run_mode,
        provider=provider,
        provider_factory=provider_factory,
        arm_builder=arm_builder,
        tiers=tiers,
        max_steps=max_steps,
        call_budget=call_budget,
    )
    report.rows.append(SweepRow("static", static.arms["static"], "no adaptation"))

    for policy in policies:
        adaptive = run_experiment(
            task_list,
            arms=("adaptive",),
            store=store,
            run_mode=run_mode,
            provider=provider,
            provider_factory=provider_factory,
            arm_builder=arm_builder,
            tiers=tiers,
            policy=policy,
            arm_label=policy.name,
            max_steps=max_steps,
            call_budget=call_budget,
        )
        report.rows.append(SweepRow(policy.name, adaptive.arms["adaptive"], policy.note()))
    return report
