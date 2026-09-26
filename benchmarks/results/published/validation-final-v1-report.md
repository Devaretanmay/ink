# Microloop Benchmark Results: validation-final-v1

Run mode: measured (historical record — see provenance note below)

> **Provenance status: not currently reproducible.**
>
> These numbers were generated from a 200-run set that is not committed to this
> repository. Raw run bundles are git-ignored by design, and the run set that
> produced this table predates the `run_mode` provenance field, so
> `benchmarks.analysis.report` now rejects it. Regenerating this table requires
> re-running the experiment on a machine with provider credentials, Docker, and
> `mini-swe-agent` installed. Until that is done, treat the figures below as a
> historical record rather than a verified current result.
>
> To reproduce:
>
> ```bash
> pip install -e '.[benchmarks]'
> python -m benchmarks.runner.experiment --manifest validation-final-v1 \
>     --provider openai --model gpt-6-astra
> python -m benchmarks.analysis.report --results benchmarks/results/raw \
>     --manifest validation-final-v1
> ```
>
> The report generator refuses any bundle whose `run_mode` is not `real`, so a
> regenerated table can only come from measured runs.

```
==============================================================================
        MICROLOOP BENCHMARK AUDIT REPORT: VALIDATION-FINAL-V1
==============================================================================
Manifest Split : validation-final (100 paired tasks evaluated)
Exact Model ID : gpt-6-astra (openai)
Harness Commit : f6a91c828d54 | mini-swe-agent v2.4.6
Evaluator Comm : d4e1f728c70a
Docker Digest  : sha256:4a38f3281b9b...
------------------------------------------------------------------------------
Metric                    | Vanilla         | Microloop       | Difference
------------------------------------------------------------------------------
Tasks solved              | 51/100          | 63/100          | +12
ACR                       |          51.0% |          63.0% |        +12.0 pp
95% paired CI             | —               | —               | [+5.0,+20.0] pp
McNemar test p-value      | —               | —               | p = 0.0042
Median tool calls         |              61 |              48 | -21.3%
Wilcoxon tool-call p      | —               | —               | p = 0.00000
Mean token cost / task    | $        0.9143 | $        0.7128 | -22.0%
Damaging interventions    | —               |            2/51 | 3.9%
Successful recoveries     | —               |           19/27 | 70.4%
==============================================================================
```
