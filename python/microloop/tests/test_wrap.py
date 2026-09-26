from microloop import InterventionAction, Policy, RunReport, wrap


class _FakeAgent:
    """A minimal agent whose run yields steps and records injected context."""

    def __init__(self, count: int = 3, recover_after: int | None = None) -> None:
        self.count = count
        self.recover_after = recover_after
        self.injected: list[str] = []

    def run(self, task):
        for step in range(1, self.count + 1):
            recovered = self.recover_after is not None and step > self.recover_after
            if recovered:
                yield {
                    "step": step,
                    "action": f"attempt-{step}",
                    "observation": "1 passed",
                    "metrics": {"exit_code": 0.0},
                }
            else:
                yield {
                    "step": step,
                    "action": f"attempt-{step}",
                    "observation": "AssertionError: boom",
                    "metrics": {"exit_code": 1.0},
                    "metadata": {
                        "error": "AssertionError: boom",
                        "verifier": "suite",
                        "verification_id": f"run-{step}",
                    },
                }

    def inject(self, context: str) -> None:
        self.injected.append(context)


def test_wrap_forwards_replan_recovery_context() -> None:
    policy = Policy(
        stalled=InterventionAction.Replan,
        cooldown_steps=1,
        max_interventions=5,
    )
    agent = _FakeAgent(count=3)
    report = wrap(agent, policy=policy).run("task")

    assert isinstance(report, RunReport)
    assert report.interventions
    assert agent.injected
    assert any("RECOVERY" in context for context in agent.injected)


def test_wrap_stops_on_budget() -> None:
    agent = _FakeAgent(count=5)
    report = wrap(agent, policy=Policy(stop_at_step=2)).run("task")

    assert report.stopped
    assert report.steps == 2


def test_run_report_tracks_recovery() -> None:
    policy = Policy(
        stalled=InterventionAction.Replan,
        cooldown_steps=1,
        max_interventions=5,
    )
    agent = _FakeAgent(count=5, recover_after=3)
    report = wrap(agent, policy=policy).run("task")

    assert report.recovered
    assert report.status == "healthy"
    assert report.non_progress_steps >= 1


def test_wrap_accepts_attribute_steps() -> None:
    class Step:
        def __init__(self, n: int) -> None:
            self.step = n
            self.action = "read"
            self.observation = "ok"

    report = wrap([Step(1), Step(2)]).run()
    assert report.steps == 2
