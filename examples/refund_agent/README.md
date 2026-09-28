# Refund decision JIT example

Run from the repository root after installing this checkout:

```bash
python -m examples.refund_agent.agent --output .microloop/refund-exact
python -m examples.refund_agent.agent --output .microloop/refund-laya \
  --engine laya --checkpoint /absolute/local/checkpoint
```

Both commands use generated cases and a labelled deterministic fallback fixture.
The Laya variant performs real checkpoint inference, but the original fallback is
still a fixture. Neither is customer-production evidence.

For a real original model, set `MICROLOOP_API_KEY` and optionally
`MICROLOOP_API_BASE` to an OpenAI-compatible endpoint, then add `--model MODEL`.
The run makes thousands of calls; choose a model and account appropriate for that
experiment. Transient 429/5xx responses retry with backoff; persistent failures
abort visibly. No fabricated usage hides failures.

Each fresh output directory receives a SQLite decision database, an independently
executed action ledger, complete exported history, and a report. The workload
runs 3,000 baseline observations, 600 shadow cases, 600 active cases, novel cases,
and deliberate drift. The Laya variant widens the evaluation window for its small
qualified region. Qualification failures remain visible; the script does not
force promotion to finish the demonstration.

The verifier replays candidate and fallback actions in isolated ledgers. Generated
cases contain three common states plus a low-frequency enterprise state (~5%)
that stays fallback for lack of calibration support, plus novel amounts and
inconsistent merchant configuration. Drift lowers the ledger's allowed refund amount; previous decisions
then lose outcome quality. The supplied requirements are permissive experimental
settings for demonstrating mechanics, not production recommendations.

The report includes cold/steady phase timing, actual fallback sources and usage,
outcome quality, profile/evaluation evidence, and lifecycle events. Do not turn
fixture fallback counts into claimed frontier-model savings.
