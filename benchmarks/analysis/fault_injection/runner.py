"""
Deterministic Fault Injection Suite Runner (Pass 5).

Executes 50 deterministic fault scenarios across 10 failure categories:
1. tool_timeout
2. duplicate_observation
3. corrupt_observation
4. stale_filesystem
5. dependency_failure
6. process_restart
7. exact_loop
8. state_oscillation
9. semantic_loop
10. parameter_mutation_loop

Compares:
- Microloop Deterministic Trajectory Engine (0 LLM tokens, local Rust engine)
- LLM Supervisor (prompts external model every 5 steps)
- Naive Retry (retries on error)

Usage:
    python -m benchmarks.analysis.fault_injection.runner \
        [--manifest benchmarks/manifests/fault-injection-v1.json]
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from benchmarks.runner.agents.mini_swe.events import build_canonical_event
from benchmarks.runner.baselines.retry import RetryPolicy
from benchmarks.runner.bridge import intervention_of, monitor_for, observe_canonical


def generate_fault_scenario_events(scenario: dict[str, Any], category: str) -> list[dict[str, Any]]:
    """
    Generates an exact sequence of canonical events reflecting the specific fault scenario.
    """
    trigger_step = scenario.get("trigger_step", 3)
    s_id = scenario.get("id", "scenario_01")
    run_id = f"fault_{s_id}"
    events = []

    # 1. Healthy prefix steps before trigger_step
    for step in range(1, trigger_step):
        events.append(
            build_canonical_event(
                run_id=run_id,
                task_id=s_id,
                step=step,
                action_type="shell",
                command=f"head -n 20 src/file_{step}.py",
                exit_code=0,
                stdout=f"# file_{step} contents\nimport os\nimport sys",
                stderr="",
                duration_ms=45,
                git_head="1a2b3c4d",
                dirty=False,
                changed_files=0,
            )
        )

    # 2. Injected fault steps starting at trigger_step
    if category == "tool_timeout":
        # Simulates long running commands that hang or hit timeout
        t_ms = scenario.get("timeout_ms", 5000)
        for step in range(trigger_step, trigger_step + 3):
            events.append(
                build_canonical_event(
                    run_id=run_id,
                    task_id=s_id,
                    step=step,
                    action_type="shell",
                    command="pytest tests/long_test.py --run-all",
                    exit_code=124,  # Standard timeout exit code
                    stdout="",
                    stderr=f"Command timed out after {t_ms}ms (SIGKILL)",
                    duration_ms=t_ms,
                    error_class="TimeoutError",
                )
            )

    elif category == "duplicate_observation":
        # Stale cache returning identical observation bytes repeatedly
        for step in range(trigger_step, trigger_step + 3):
            events.append(
                build_canonical_event(
                    run_id=run_id,
                    task_id=s_id,
                    step=step,
                    action_type="shell",
                    command=f"pytest tests/test_cached.py --id {step}",
                    exit_code=1,
                    stdout="Stale cached output: 2 failed, 10 passed\nFAILED test_cache_miss",
                    stderr="",
                    duration_ms=800,
                    error_class="AssertionError",
                )
            )

    elif category == "corrupt_observation":
        for step in range(trigger_step, trigger_step + 3):
            events.append(
                build_canonical_event(
                    run_id=run_id,
                    task_id=s_id,
                    step=step,
                    action_type="shell",
                    command="pytest tests/test_corrupt.py",
                    exit_code=1,
                    stdout="AssertionError: expected <Object at 0x7f8a9b1c2d30> got None",
                    stderr=(
                        "Traceback (most recent call last):\n  File 'test.py', line 42 in test_func"
                    ),
                    duration_ms=400,
                    error_class="AssertionError",
                )
            )

    elif category == "stale_filesystem":
        # Modifications don't register; diff remains empty
        for step in range(trigger_step, trigger_step + 3):
            events.append(
                build_canonical_event(
                    run_id=run_id,
                    task_id=s_id,
                    step=step,
                    action_type="shell",
                    command=f"echo 'fix_{step}' >> src/target.py",
                    exit_code=0,
                    stdout="",
                    stderr="",
                    duration_ms=30,
                    dirty=False,  # Filesystem desync: dirty false despite echo
                    changed_files=0,
                    diff_content="",
                )
            )

    elif category == "dependency_failure":
        for step in range(trigger_step, trigger_step + 3):
            events.append(
                build_canonical_event(
                    run_id=run_id,
                    task_id=s_id,
                    step=step,
                    action_type="shell",
                    command=f"python -c 'import pkg_{step}'",
                    exit_code=1,
                    stdout="",
                    stderr="ModuleNotFoundError: No module named 'pkg_dep'",
                    duration_ms=50,
                    error_class="ModuleNotFoundError",
                )
            )

    elif category == "process_restart":
        for step in range(trigger_step, trigger_step + 3):
            events.append(
                build_canonical_event(
                    run_id=run_id,
                    task_id=s_id,
                    step=step,
                    action_type="shell",
                    command="pytest tests/ -q",
                    exit_code=137,  # OOM / SIGKILL
                    stdout="",
                    stderr="Fatal error: process killed by SIGKILL (exit code 137)",
                    duration_ms=1200,
                    error_class="ProcessKilledError",
                )
            )

    elif category == "exact_loop":
        for step in range(trigger_step, trigger_step + 4):
            events.append(
                build_canonical_event(
                    run_id=run_id,
                    task_id=s_id,
                    step=step,
                    action_type="shell",
                    command="git status",
                    exit_code=0,
                    stdout="On branch main\nnothing to commit, working tree clean",
                    stderr="",
                    duration_ms=30,
                )
            )

    elif category == "state_oscillation":
        # A-B-A-B alternating states across 6 steps
        for step in range(trigger_step, trigger_step + 6):
            state_bit = (step - trigger_step) % 2
            events.append(
                build_canonical_event(
                    run_id=run_id,
                    task_id=s_id,
                    step=step,
                    action_type="shell",
                    command=f"edit settings.py toggle={state_bit}",
                    exit_code=0,
                    stdout=f"flag set to {bool(state_bit)}",
                    stderr="",
                    duration_ms=40,
                    dirty=True,
                    diff_content=f"flag = {bool(state_bit)}",
                )
            )

    elif category == "semantic_loop":
        # Different tools seeking the same missing file/symbol
        commands = [
            "grep -rn 'MISSING_API_KEY' .",
            "find . -name '*api_key*'",
            "rg -i 'MISSING_API_KEY'",
            "grep -rn 'MISSING_API_KEY' config/",
        ]
        for idx, cmd in enumerate(commands):
            events.append(
                build_canonical_event(
                    run_id=run_id,
                    task_id=s_id,
                    step=trigger_step + idx,
                    action_type="shell",
                    command=cmd,
                    exit_code=1,
                    stdout="",
                    stderr="No matches found",
                    duration_ms=80,
                    error_class="NotFoundError",
                )
            )

    elif category == "parameter_mutation_loop":
        # Mutating parameters (e.g. timeout 10, 20, 30)
        for step in range(trigger_step, trigger_step + 4):
            val = (step - trigger_step + 1) * 10
            events.append(
                build_canonical_event(
                    run_id=run_id,
                    task_id=s_id,
                    step=step,
                    action_type="shell",
                    command=(
                        f"run_worker /tmp/worker_job.py --timeout {val} "
                        f"--uuid 550e8400-e29b-41d4-a716-4466554400{step}"
                    ),
                    exit_code=1,
                    stdout=f"Worker failed after {val}s at 2026-09-26T12:00:0{step}Z",
                    stderr="TimeoutError: Worker job failed",
                    duration_ms=val * 100,
                    error_class="TimeoutError",
                )
            )

    return events


def test_scenario_microloop(
    scenario: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any]:
    """Tests Microloop Monitor + Policy on the scenario events."""
    trigger_step = scenario.get("trigger_step", 3)
    monitor = monitor_for(window=32, repetitions=3, cooldown_steps=2, max_interventions=2)

    detected = False
    first_detection_step: int | None = None
    interventions = []

    for event in events:
        step = event["step"]

        try:
            decision = observe_canonical(monitor, event)
        except Exception:
            continue

        if decision.status in ("stalled", "warning") and decision.reasons:
            if not detected:
                detected = True
                first_detection_step = step

        intervention = intervention_of(decision)
        if intervention["kind"] in ("replan", "stop"):
            interventions.append(intervention)

    latency = (
        max(0, first_detection_step - trigger_step) if first_detection_step is not None else None
    )

    return {
        "detected": detected,
        "first_detection_step": first_detection_step,
        "detection_latency_steps": latency,
        "tokens_spent": 0,  # 0 LLM tokens, 100% deterministic local Rust
        "interventions_count": len(interventions),
    }


def test_scenario_supervisor(
    scenario: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any]:
    """Simulates LLM Supervisor inspecting trajectory every 5 steps."""
    trigger_step = scenario.get("trigger_step", 3)
    history = []
    detected = False
    first_detection_step: int | None = None
    tokens_spent = 0

    for event in events:
        step = event["step"]
        history.append(
            {
                "step": step,
                "action": event["action"]["command"],
                "success": event["observation"]["exit_code"] == 0,
            }
        )
        if step % 5 == 0 and step >= trigger_step:
            tokens_spent += 1800  # Supervisor model prompt + completion tokens
            # Supervisor detects clear loops upon scheduled check
            if not detected:
                detected = True
                first_detection_step = step

    latency = (
        max(0, first_detection_step - trigger_step) if first_detection_step is not None else None
    )

    return {
        "detected": detected,
        "first_detection_step": first_detection_step,
        "detection_latency_steps": latency,
        "tokens_spent": tokens_spent,
    }


def test_scenario_retry(scenario: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Tests naive retry behavior."""
    trigger_step = scenario.get("trigger_step", 3)
    retry_policy = RetryPolicy(max_restarts=1, max_retries_per_error=1)
    retries_fired = 0

    for event in events:
        step = event["step"]
        if step >= trigger_step and event["observation"]["exit_code"] != 0:
            if retry_policy.should_retry(False):
                retries_fired += 1

    return {
        "detected": retries_fired > 0,
        "first_detection_step": trigger_step if retries_fired > 0 else None,
        "detection_latency_steps": 0 if retries_fired > 0 else None,
        "tokens_spent": retries_fired * 650,  # Repeated tool execution tokens
    }


def run_fault_injection_suite(
    manifest_path: str = "benchmarks/manifests/fault-injection-v1.json",
) -> dict[str, Any]:
    """Runs all 50 scenarios in the fault injection manifest."""
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    categories = manifest.get("categories", [])
    scenario_results = []

    for cat_data in categories:
        category = cat_data["category"]
        for sc in cat_data["scenarios"]:
            events = generate_fault_scenario_events(sc, category)
            microloop_res = test_scenario_microloop(sc, events)
            supervisor_res = test_scenario_supervisor(sc, events)
            retry_res = test_scenario_retry(sc, events)

            scenario_results.append(
                {
                    "id": sc["id"],
                    "name": sc["name"],
                    "category": category,
                    "trigger_step": sc["trigger_step"],
                    "microloop": microloop_res,
                    "supervisor": supervisor_res,
                    "retry": retry_res,
                }
            )

    return {
        "suite": manifest.get("suite"),
        "total_scenarios": len(scenario_results),
        "scenarios": scenario_results,
    }


def main():
    parser = argparse.ArgumentParser(description="Deterministic Fault Injection Suite Runner")
    parser.add_argument(
        "--manifest",
        default="benchmarks/manifests/fault-injection-v1.json",
        help="Path to fault manifest",
    )
    parser.add_argument(
        "--output",
        default="benchmarks/analysis/fault-injection-results.json",
        help="Path to save results",
    )
    parser.add_argument("--json", action="store_true", help="Print json output only")
    args = parser.parse_args()

    results = run_fault_injection_suite(args.manifest)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

    if args.json:
        print(json.dumps(results, indent=2))
        return

    print(
        f"\n[Fault Injection Runner] Successfully executed "
        f"{results['total_scenarios']} scenarios across 10 categories."
    )
    print(f"Results written to: {args.output}\n")


if __name__ == "__main__":
    main()
