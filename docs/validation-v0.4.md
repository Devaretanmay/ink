# Validation evidence (v0.4)

## Task map

- Task 1, decision primitive and compatibility: `python/microloop/tests/test_decision_jit.py`
- Task 2, local history and profiler: `python/microloop/tests/test_task2_history.py`
- Task 3, candidate compilation and dispatch: `python/microloop/tests/test_laya_path.py`,
  `docs/laya-path.md`
- Task 4, shadow verification and re-evaluation: `python/microloop/tests/test_task4_lifecycle.py`
- Task 5, operational demo and CLI: `examples/refund_agent/agent.py`, this file

## Refund workload (exact engine, fixture fallback)

Reproduce from the repository root after installing this checkout:

```bash
python -m examples.refund_agent.agent --output .microloop/validation-exact-v05
```

Latest run (arm64, macOS): 3,000 baseline observations at quality 1.0, promotion
qualified, 410 of 600 active decisions served as `fast_path` at quality 1.0, all 6
novel cases fell back, drift (ledger limit 100 to 10) demoted the path to SHADOW
with quality 0.57, and post-demotion decisions fell back. Three common regions
qualified; the ~5% enterprise state keeps falling back for lack of calibration
support (`uncovered_rate` 0.05). The fallback is a labelled deterministic fixture
(`model_calls` 0, `provider` fixture), so this run proves mechanics, not
frontier-model savings. Reports under `.microloop/` are git-ignored and never
committed; the ledger, exported history, and report travel with each run directory.

## Laya evidence

`test_laya_path.py` compiles a Laya candidate from 200 observed text decisions,
promotes to ACTIVE, reopens the database, serves known states as `fast_path`,
falls back on novel requests and engine failure with the artifact intact
(`docs/laya-path.md` for package, checkpoint, and smoke measurements). The
checked-in Laya refund-agent report instead shows SHADOW: structured numeric
states are out of distribution for the text decision model, so it never
qualified there. That non-promotion is the system working, not a gap.

## Real-model status: blocked, not attempted

No provider credential exists in this environment, so the `--model` path has
never run here. The code records actual provider usage when it does
(`agent.py: model_fallback`), but every saved report uses the fixture. Any
claim of measured model-call reduction requires a credentialed run first.

## Metric honesty

`model_calls_avoided` is always null; fallback invocations need not equal model
calls. Fast-path counts, usage sums over known fields only, and outcome deltas
are reported separately. Missing outcomes stay distinct from unsuccessful ones.
