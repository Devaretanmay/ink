# Benchmarks

Microloop is evaluated against the same agent, model and prompt with Microloop
as the only variable. This directory holds the reproducible methodology, the
runner and the published summaries. Raw runs are not committed.

## Layout

```
benchmarks/
├── manifests/    frozen task manifests (dev, validation-pilot, validation-final)
├── runner/       experiment runner, agent adapters, baselines, result schemas
└── analysis/     reporting, statistics, detector + fault-injection evaluation
```

Published summaries are committed under `benchmarks/results/published/`. Raw run
bundles are written to `benchmarks/results/raw/`, which is git-ignored.

## Methodology

- **Conditions.** Vanilla (no monitoring), retry baseline, LLM supervisor and
  Microloop. All share one step, wall-clock and token budget.
- **Task freezing.** Manifests are frozen before testing, independently of
  outcome. Validation tasks are disjoint from development tasks.
- **Interleaving.** Conditions are randomized within each task so provider
  drift does not favour one arm.
- **Reporting.** Task-level paired bootstrap confidence intervals on completion
  rate, plus tool-call and token spend on failed or stalled trajectories.

## Headline result

`validation-final-v1` (100 paired SWE-bench Verified tasks, frozen manifest,
`gpt-6-astra`, mini-swe-agent v2.4.6):

| Metric            | Vanilla | Microloop | Difference |
|-------------------|---------|-----------|------------|
| Tasks solved      | 51/100  | 63/100    | +12        |
| ACR               | 51.0%   | 63.0%     | +12.0 pp   |
| 95% paired CI     | —       | —         | [+5.0,+20.0] pp |
| Median tool calls | 61      | 48        | −21.3%     |
| Damaging interventions | —  | 2/51      | 3.9%       |

Full report and provenance: `benchmarks/results/published/validation-final-v1-report.md`.

## Reproducing

```bash
python -m benchmarks.runner.experiment --manifest validation-pilot-v1 --dry-run
python -m benchmarks.analysis.report --results benchmarks/results/raw --manifest validation-final-v1
```

Raw outputs are written to `benchmarks/results/raw/`. Only small summaries with
explicit provenance are committed.
