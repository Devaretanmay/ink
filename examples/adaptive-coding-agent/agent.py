"""One task, run static and adaptive, showing where adaptation helps and hurts.

Fully offline and deterministic, so it is safe to run in CI. It runs two tasks
chosen because they disagree about the answer: one the agent can solve on its
own, where Microloop's intervention is pure cost, and one where it is a close
call and the timing of the intervention decides the run.

Run with:

    python examples/adaptive-coding-agent/agent.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from integrations.coding_harness.harness import CodingHarness  # noqa: E402
from integrations.coding_harness.providers import (  # noqa: E402
    AgentBehaviour,
    Ceiling,
)
from integrations.coding_harness.tasks import build_tasks  # noqa: E402
from integrations.experiment.runner import (  # noqa: E402
    DEFAULT_TIERS,
    POLICIES,
    _simulated_factory,
    _tier_by_model,
    build_session,
)


def _task(kind: str):
    """A one-off task whose agent behaves a specific way."""
    template = build_tasks(1)[0]
    ceiling = Ceiling.Plausible if kind == "hopeless" else Ceiling.Correct
    patience = 1 if kind == "recovers" else 3
    template.behaviour = AgentBehaviour(
        patience=patience,
        ceiling=ceiling,
        distractors=template.behaviour.distractors,
        settle=1 if kind == "recovers" else 3,
    )
    return template


def run(arm: str, task, policy=None):  # noqa: ANN001
    session = build_session(arm, policy=policy)
    provider = _simulated_factory(_tier_by_model(DEFAULT_TIERS))(task)
    harness = CodingHarness(
        task=task, provider=provider, session=session, arm=arm, max_steps=40
    )
    return session, provider, harness.run()


def _show(label: str, arm: str, task, policy=None) -> bool:  # noqa: ANN001
    session, provider, result = run(arm, task, policy)
    print(f"{label}")
    for line in result.transcript:
        print(f"  {line}")
    print(
        f"  -> success={result.success} steps={result.steps} "
        f"adaptations={result.adaptations} model={session.adapter.model}"
    )
    if provider.interruptions:
        print(
            f"  -> the agent was knocked off its approach {provider.interruptions} "
            f"time(s) and ended on rung {provider.rung}"
        )
    print()
    return result.success


def main() -> int:
    print("A task the agent solves unaided. Microloop's intervention buys nothing.")
    print("=" * 72)
    easy = _task("recovers")
    _show("static", "static", easy)
    _show("adaptive", "adaptive", easy)

    print("A close call. Replanning on every stalled step loses the run.")
    print("=" * 72)
    hard = _task("stubborn")
    static_ok = _show("static", "static", hard)
    eager_ok = _show("adaptive (eager replan)", "adaptive", hard, POLICIES[0])
    wary_ok = _show("adaptive (wary replan)", "adaptive", hard, POLICIES[2])

    print("=" * 72)
    print(f"static succeeded:        {static_ok}")
    print(f"eager replan succeeded:  {eager_ok}")
    print(f"wary replan succeeded:   {wary_ok}")
    if static_ok and not eager_ok:
        print()
        print(
            "Intervening as fast as the run stalls made this task worse. That is "
            "the result worth knowing, and it is why the sweep reports every "
            "policy rather than only the best one."
        )
    return 0


def check() -> int:
    """Assert the results above, so CI fails if the model stops behaving this way.

    This is a regression guard, not a claim. It pins two things the rest of the
    pass depends on: that the simulated agent is not rigged (it can lose, and
    doing nothing beats a bad policy), and that a restrained policy is better
    than an eager one. If either stops holding, the experiment underneath is no
    longer measuring what the documentation claims it measures.
    """
    easy = _task("recovers")
    _, _, easy_static = run("static", easy)
    _, _, easy_adaptive = run("adaptive", easy)
    hard = _task("stubborn")
    _, _, hard_static = run("static", hard)
    _, _, hard_eager = run("adaptive", hard, POLICIES[0])
    _, _, hard_wary = run("adaptive", hard, POLICIES[2])

    problems = []
    if not easy_static.success or not easy_adaptive.success:
        problems.append("an agent that recovers unaided should succeed in both arms")
    if not hard_static.success:
        problems.append("the stubborn task should be solvable without intervention")
    if hard_eager.success:
        problems.append(
            "eager replanning unexpectedly beat doing nothing; the harness may "
            "have stopped modelling interruption, which would make the "
            "experiment meaningless"
        )
    if not hard_wary.success:
        problems.append("a restrained policy should finish the stubborn task")
    for problem in problems:
        print(f"[fail] {problem}")
    if not problems:
        print("[ok] the simulated agent can win and lose, and restraint beats speed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main() or check())
