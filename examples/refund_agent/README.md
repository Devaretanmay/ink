The default engine is now the bundled Ink Decision v1 neural runtime.
Run `ink model-install` once before the demo. `--engine exact` is only an
explicit deterministic test reference. Historical results below retain their
original runtime identity.

# Refund decision JIT example

Run from the repository root after installing this checkout:

```bash
python -m examples.refund_agent.agent --output .ink/refund-exact --engine exact
python -m examples.refund_agent.agent --output .ink/refund-decision \
  --engine decision --checkpoint /absolute/local/checkpoint
```

Both commands use generated cases and a labelled deterministic fallback fixture.
The decision variant performs real checkpoint inference, but the original fallback is
still a fixture. Neither is customer-production evidence.

For a real original model, set `INK_API_KEY` and optionally
`INK_API_BASE` to an OpenAI-compatible endpoint, then add `--model MODEL`.
The run makes thousands of calls; choose a model and account appropriate for that
experiment. Transient 429/5xx responses retry with backoff; persistent failures
abort visibly. No fabricated usage hides failures.

Each fresh output directory receives a SQLite decision database, an independently
executed action ledger, complete exported history, and a report. The workload
runs 3,000 baseline observations, 600 shadow cases, then active, novel, drift,
and after-demotion phases sized by the evaluation window (600 exact; 1,800 decision,
see the agent REQUIREMENTS override). Qualification failures remain visible; the
script does not force promotion to finish the demonstration.

The verifier replays candidate and fallback actions in isolated ledgers. Generated
cases contain three common states plus a low-frequency enterprise state (~5%)
that stays fallback for lack of calibration support, plus novel amounts and
inconsistent merchant configuration. Drift lowers the ledger's allowed refund amount; previous decisions
then lose outcome quality. The supplied requirements are permissive experimental
settings for demonstrating mechanics, not production recommendations.

The report includes cold/steady phase timing, actual fallback sources and usage,
outcome quality, profile/evaluation evidence, and lifecycle events. Do not turn
fixture fallback counts into claimed frontier-model savings.

A credential-free real-model run can use a pre-existing local MLX checkpoint:

```bash
uv pip install 'mlx-lm==0.31.3'
python -m examples.refund_agent.agent --output .ink/real-local \
  --engine decision --checkpoint /absolute/decision/checkpoint \
  --local-model /absolute/qwen/checkpoint --require-lifecycle
```

The local fallback caches attention prefixes, not answers. Each fallback still
performs a fresh model forward pass and produces a token. Decision experiments use
1,800 active/drift cases to collect adequate randomized comparison evidence at
low coverage. `--require-lifecycle` makes missing lifecycle gates fail CI.
