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
support (`uncovered_rate` 0.05). The fixture fallback declares `model_calls` 0 in
the site contract, so avoided calls report as `0`, never as savings. Reports
under `.microloop/` are git-ignored and never committed; the ledger, exported
history, and report travel with each run directory. The Laya variant of the same
workload widens the evaluation window for its small qualified region; see the
agent source and README.

## Laya evidence

`test_laya_path.py` compiles a Laya candidate from 200 observed text decisions,
promotes to ACTIVE, reopens the database, serves known states as `fast_path`,
falls back on novel requests and engine failure with the artifact intact
(`docs/laya-path.md` for package, checkpoint, and smoke measurements). The
checked-in Laya refund-agent report instead shows SHADOW: structured numeric
states are out of distribution for the text decision model, so it never
qualified there. That non-promotion is the system working, not a gap.

## Real-model status

Two live fallbacks exist; both label provenance and neither is production traffic.

- `--local-model CHECKPOINT`: real autoregressive MLX generation, one fresh token
  per call, measured input/output tokens, `model_calls` 1, `provider` local-mlx.
  Full run (Qwen2.5-Coder-3B-Instruct-4bit, 4,809 decisions): 4,277 measured model
  calls, 442,723 input tokens, baseline quality 0.683, 2 regions qualified, 271
  fast-path serves, novel cases fell back, drift demoted to SHADOW, 532 declared
  calls avoided (398 verified). The imperfect baseline is the finding: weak
  regions never qualify, strong ones do.
- `--model MODEL`: OpenAI-compatible HTTP fallback recording provider usage.
  Smoked live against Groq (`openai/gpt-oss-20b`): key valid, 3 of 4 states
  correct, per-call token usage captured. Findings: provider WAF blocks the
  default urllib agent (fixed with an explicit `User-Agent`); transient 429s and
  socket timeouts occur even at low pace (fixed with documented backoff retry).
  A full 4,809-call Groq run died at 443 decisions on a socket timeout before the
  retry fix; it has not been rerun yet. Rotate any key pasted into chat.

Any claim of measured model-call reduction requires a completed run first.
Fixture runs prove mechanics only.

## Metric honesty

`model_calls_avoided` reports only under a declared fixed-call site contract
(`fallback_model_calls`); otherwise it stays null. Fallback invocations need not
equal model calls. Fast-path counts, usage sums over known fields only, and
outcome deltas are reported separately. Missing outcomes stay distinct from
unsuccessful ones.
