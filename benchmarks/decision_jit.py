"""Repeatable Ink Decision v1 load/inference measurements; never asserts latency claims."""

import argparse
import json
import platform
import resource
import statistics
import time
from pathlib import Path

from ink import DecisionSite, Ink
from ink.internal.contracts import canonical
from ink.internal.engines import DecisionModelEngine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    site = DecisionSite("benchmark.decision", {"request": "string"}, ("refund", "specialist"))
    state = {"request": "I was billed twice. Please refund the duplicate charge."}
    engine = DecisionModelEngine(args.checkpoint)
    start = time.perf_counter()
    artifact = engine.compile(site, [{"state": state, "choice": "refund"}])
    cold = time.perf_counter() - start
    timings = []
    for _ in range(30):
        start = time.perf_counter()
        choice, probability = engine.predict(artifact, state)
        timings.append(time.perf_counter() - start)
    with Ink(":memory:") as client:
        dispatch = []
        for _ in range(100):
            start = time.perf_counter()
            client.decide(site=site, state=state, fallback=lambda: "refund")
            dispatch.append(time.perf_counter() - start)
    root = Path(args.checkpoint)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if platform.system() != "Darwin":
        rss *= 1024
    result = {
        "hardware": platform.platform(),
        "machine": platform.machine(),
        "checkpoint": str(root.resolve()),
        "runtime": artifact["runtime_version"],
        "cold_compile_load_and_first_inference_seconds": cold,
        "warm_inference_p50_seconds": statistics.median(timings),
        "warm_inference_p95_seconds": sorted(timings)[28],
        "warm_samples": len(timings),
        "observe_dispatch_p50_seconds": statistics.median(dispatch),
        "artifact_descriptor_bytes": len(canonical(artifact).encode()),
        "checkpoint_bytes": sum((root / name).stat().st_size for name in artifact["manifest"]),
        "process_peak_rss_bytes": rss,
        "prediction": choice,
        "raw_probability": probability,
        "scope": "Local smoke measurement; generated state, no frontier-model benchmark",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
