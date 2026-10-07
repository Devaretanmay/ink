# Ink Benchmarks

Reproducible evaluation of the Ink Decision JIT: Fast Path serving, qualification, drift
demotion, and economics on bounded decision workloads.

> **Scope.** These benchmarks are **controlled and largely synthetic**. Workloads and
> policy-drift injections are generated for reproducibility, not sampled from a customer
> deployment. Treat the numbers as behavior on these workloads, not as guarantees for
> production traffic. Every external claim lives, with its evidence path and scope, in
> [docs/claims.md](../docs/claims.md).

---

## Primary suite

The canonical comparison — Ink vs. exact cache, semantic cache, a cheaper model, and a
small classifier under temporal policy drift:

```bash
PYTHONPATH=python/ink python -m benchmarks.competitive_frontier.run
```

- Published report: [`results/competitive_frontier/REPORT.md`](results/competitive_frontier/REPORT.md)
- Summary metrics: [`results/competitive_frontier/summary.json`](results/competitive_frontier/summary.json)

Headline results are summarized, with scope, in the project [README](../README.md) and
[docs/claims.md](../docs/claims.md).

---

## Other suites

| Suite | Focus | Result artifact |
| :--- | :--- | :--- |
| [`benchmarks/release_candidate.py`](release_candidate.py) | Serving latency, fallback overhead, kill-switch overhead, qualification cost, storage growth, concurrency, retention | Writes a JSON results file to the output directory you pass |
| [`benchmark_qualification_efficiency.py`](benchmark_qualification_efficiency.py) | Sample efficiency of concentration bounds (Hoeffding, Empirical Bernstein, Howard et al.) | `results/qualification_efficiency.json` |
| [`benchmark_long_horizon.py`](benchmark_long_horizon.py) | Long-horizon cumulative model-call avoidance | `results/long_horizon_economics.json` |
| [`benchmark_agent_site_selection.py`](benchmark_agent_site_selection.py) | Selective compilation vs. compile-every-site | `results/agent_site_selection.json` |
| [`public_support/evaluate.py`](public_support/evaluate.py) | Public BANKING77 intent calibration probe | [public_support/README.md](public_support/README.md) |
| [`validate_semantic_lifecycle.py`](validate_semantic_lifecycle.py) | Sparse semantic coverage, margin bounds, counterexample contraction | Test evidence |

Live-provider validation (`benchmark_real_world.py`) requires `GROQ_API_KEY` and makes real
API calls. It is not part of the default suite.

---

## Running

```bash
PYTHONPATH=python/ink python benchmarks/benchmark_qualification_efficiency.py
PYTHONPATH=python/ink python benchmarks/benchmark_long_horizon.py
PYTHONPATH=python/ink python benchmarks/benchmark_agent_site_selection.py
PYTHONPATH=python/ink python benchmarks/release_candidate.py --output .ink/rc-bench
```

Schemas for run bundles live in [`schemas/`](schemas/). Measured claims must be
reproducible and are registered in [docs/claims.md](../docs/claims.md).
