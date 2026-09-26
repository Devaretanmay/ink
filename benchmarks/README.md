# Benchmarks

Microloop is evaluated against the same agent, model and prompt with Microloop
as the only variable. This directory holds the reproducible methodology, the
runner and the published summaries. Raw runs are not committed.

## Layout

```
benchmarks/
├── manifests/    frozen task manifests (dev, validation-pilot, validation-final)
├── runner/       experiment runner, agent adapters, baselines
├── analysis/     reporting, statistics, detector + fault-injection evaluation
├── schemas/      authoritative run-result and canonical-event schemas
└── results/      derived/ + published/ summaries; raw/ is git-ignored
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

## Evidence status

**No result in this directory is currently verified, and none is presented as a
headline.** A previous 100-task SWE-bench Verified run is recorded in
`results/published/validation-final-v1-report.md`, but its raw bundles are
git-ignored and predate the `run_mode` provenance field, so the report generator
rejects it. That file is kept as a labelled historical record only. Its numbers
are not repeated here, because this directory cannot substantiate them.

Regenerating a real result requires provider credentials, Docker and
`mini-swe-agent`. See [Reproducing](#reproducing).

## Provenance gate

Every run bundle declares `run_mode`:

| Value | Meaning |
|---|---|
| `real` | An agent actually executed the task. This is the only evidence. |
| `simulated` | The offline deterministic generator produced it. Never evidence. |

Simulated runs are written to `results/raw/simulated/`, separate from real
output, and the runner refuses to produce them unless `--dry-run` or a
`mock`/`offline` provider is passed explicitly. `report.py` rejects any bundle
that is not `run_mode=real`; `--allow-simulated` overrides that and stamps the
report `SIMULATED - NOT EVIDENCE`.

## Reproducing

```bash
pip install -e '.[benchmarks]'
python -m benchmarks.runner.experiment --manifest validation-final-v1 \
    --provider openai --model gpt-6-astra
python -m benchmarks.analysis.report --results benchmarks/results/raw \
    --manifest validation-final-v1
```

Raw outputs are written to `benchmarks/results/raw/` (real) or
`benchmarks/results/raw/simulated/` (simulated). Both are git-ignored. Only
small summaries with explicit provenance are committed.

## Analyses requiring external inputs

Three scripts in `analysis/` have no committed input data and cannot run
as-is. They are retained because they define the methodology; supply the
corresponding raw run directories to use them:

| Script | Needs |
|---|---|
| `analysis/cross_model.py` | Two multi-model result directories, compared via `--model-a` / `--model-b` |
| `analysis/evaluate_detectors.py` | A labelled trajectory directory with known loop/non-loop runs |
| `analysis/classify.py` | A raw run directory to classify failure modes over |

`analysis/report.py`, `analysis/stats.py` and `analysis/fault_injection/` are
self-sufficient against a results tree and the committed manifests.
