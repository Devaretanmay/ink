"""
Runtime microbenchmark for the Microloop engine.

This measures the cost of calling `Monitor.observe()`, not agent performance. It
produces no benchmark result and nothing here is a claim about task success. The
numbers quoted in docs/legacy/README-v0.3.md ("Cost") come from this script, so
they can be re-derived rather than taken on trust.

    make perf

Three scenarios, all with the window full (32 records) so every detector runs:

    healthy        distinct commands, exit 0. The common case.
    improving      a verifier reporting a falling failure count.
    repeating      the same failing test over and over. Every detector fires.

Absolute timings are machine-specific. The shape that matters is the ratio
between the scenarios, and that memory does not grow with run length.
"""
from __future__ import annotations

import argparse
import platform
import statistics
import time
import tracemalloc

from microloop import Monitor

STEPS = 100_000
WARMUP = 5_000


def healthy(i: int) -> dict:
    return {
        "action": f"read module_{i}.py",
        "observation": f"contents of module_{i}.py ({i} lines)",
        "metrics": {"exit_code": 0.0},
        "step": i,
    }


def improving(i: int) -> dict:
    return {
        "action": "pytest tests/",
        "observation": "running",
        "metrics": {"exit_code": 1.0, "failures": max(0, 400 - i // 10)},
        "metadata": {"verifier": "pytest", "verification_id": f"run-{i}"},
        "step": i,
    }


def repeating(i: int) -> dict:
    return {
        "action": "pytest tests/",
        "observation": "4 failed, 21 passed",
        "metrics": {"exit_code": 1.0, "failures": 4},
        "metadata": {
            "verifier": "pytest",
            "verification_id": f"run-{i}",
            "error": "AssertionError: test_admin.py:42",
        },
        "step": i,
    }


SCENARIOS = {"healthy": healthy, "improving": improving, "repeating": repeating}


def measure(make, steps: int) -> tuple[float, float, float]:
    monitor = Monitor()
    for i in range(1, WARMUP + 1):
        monitor.observe(**make(i))
    samples: list[int] = []
    for i in range(WARMUP + 1, WARMUP + steps + 1):
        start = time.perf_counter_ns()
        monitor.observe(**make(i))
        samples.append(time.perf_counter_ns() - start)
    samples.sort()
    n = len(samples)
    return (
        statistics.median(samples) / 1000,
        samples[int(n * 0.99)] / 1000,
        samples[-1] / 1000,
    )


def memory(steps: int) -> float:
    """Traced allocation after many steps, to show the window stays bounded."""
    monitor = Monitor()
    for i in range(1, steps + 1):
        monitor.observe(
            action=f"command number {i}",
            observation="output line " * 20,
            step=i,
        )
    traced, _peak = tracemalloc.get_traced_memory()
    return traced / 1024


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--steps", type=int, default=STEPS)
    args = parser.parse_args()

    print(f"python {platform.python_version()} on {platform.machine()}")
    print(f"window 32 records, {args.steps:,} measured steps after {WARMUP:,} warmup\n")
    print(f"{'scenario':<12}{'median':>12}{'p99':>12}{'max':>12}")
    print(f"{'':<12}{'(us)':>12}{'(us)':>12}{'(us)':>12}")
    for name, make in SCENARIOS.items():
        median, p99, worst = measure(make, args.steps)
        print(f"{name:<12}{median:>12.1f}{p99:>12.1f}{worst:>12.1f}")

    tracemalloc.start()
    traced = memory(args.steps)
    tracemalloc.stop()
    print(f"\ntraced memory after {args.steps:,} steps: {traced:.0f} KiB")
    print("the window holds 32 records, so this does not grow with run length")


if __name__ == "__main__":
    main()
