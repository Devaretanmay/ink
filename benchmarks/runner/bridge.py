"""Bridge between the benchmark's canonical event format and the Microloop 0.3 SDK.

The benchmark telemetry schema (``build_canonical_event``) is intentionally
separate from the runtime's public event model. This module maps one to the
other so the runner, replay tool and analyses all use the shipped runtime API.
"""

from __future__ import annotations

from typing import Any

from microloop import Monitor, Policy


def monitor_for(
    window: int = 32,
    repetitions: int = 3,
    stagnation_steps: int = 8,
    observer_mode: bool = False,
    cooldown_steps: int = 3,
    max_interventions: int = 2,
):
    """Build a Monitor in observation-only or recovery mode."""
    policy = None
    if not observer_mode:
        policy = Policy(
            stalled="replan",
            regressing="replan",
            cooldown_steps=cooldown_steps,
            max_interventions=max_interventions,
        )
    return Monitor(
        window=window,
        repetitions=repetitions,
        stagnation_steps=stagnation_steps,
        policy=policy,
    )


def observe_canonical(monitor, event: dict[str, Any]):
    """Run one canonical benchmark event through the runtime."""
    action = event.get("action") or {}
    observation = event.get("observation") or {}
    workspace = event.get("workspace") or {}
    metrics_in = event.get("metrics") or {}
    step = int(event.get("step", 0))

    exit_code = int(observation.get("exit_code", 0) or 0)
    combined = f"{observation.get('stdout', '')}{observation.get('stderr', '')}"
    metrics: dict[str, float] = {"exit_code": float(exit_code)}

    failures = metrics_in.get("tests_failed")
    if failures is not None:
        metrics["failures"] = float(failures)

    metadata: dict[str, str] = {}
    error_class = observation.get("error_class")
    if error_class:
        metadata["error"] = str(error_class)
    scope = metrics_in.get("verification_scope")
    if scope:
        metadata["verifier"] = str(scope)
    if failures is not None:
        metadata.setdefault("verifier", "pytest")
        metadata["verification_id"] = f"obs_{step}"

    state = {key: str(value) for key, value in workspace.items() if value is not None}

    return monitor.observe(
        action=str(action.get("command") or action.get("type") or "shell"),
        observation=combined,
        state=state or None,
        metrics=metrics,
        metadata=metadata or None,
        step=step,
    )


def decision_to_dict(decision) -> dict[str, Any]:
    """Serialize a Decision for feature logs and summaries."""
    return {
        "step": decision.step,
        "status": decision.status,
        "reasons": list(decision.reasons),
        "intervention": decision.intervention,
        "severity": decision.severity,
        "verified_progress": decision.verified_progress,
        "feedback": decision.feedback,
    }


def intervention_of(decision) -> dict[str, str | None]:
    """Backwards-compatible intervention record: ``{"kind", "feedback"}``."""
    return {"kind": decision.intervention, "feedback": decision.feedback}
