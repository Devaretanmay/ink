# Laya fast path: feasibility, spike, measurements

## Feasibility

- Package: `laya-mlx==0.2.0`, optional dep for darwin arm64, Python >= 3.11, macOS 14+.
  Installed in both `.venv` (3.14) and `.venv313` (3.13) with `mlx 0.32.2`.
- Interface: `laya_mlx.load(checkpoint)` plus `agent.predict(state, questions)`.
  `Microloop` binds it via `LayaEngine` (`python/microloop/microloop/internal/engines.py`).
- Artifact: `model.safetensors` (843 MB) plus `rl_agent_config.json`,
  `encoder/config.json`, tokenizer files, `mlx_config.json`. Checkpoint used here:
  `models--aac6fef--laya-mlx` snapshot `20aed815`.
- Training: none. `LayaEngine.compile` configures the pretrained checkpoint with up
  to three observed examples in the question instructions. Coverage and confidence
  come from observed outcomes, not from weight updates.
- Licensing: upstream weights `convaiinnovations/laya` declare Apache-2.0. The MLX
  port is independent; the cached checkpoint ships no license file, so
  redistribution rights stay unverified.

## Spike

```bash
.venv313/bin/python -m pytest python/microloop/tests/test_laya_path.py -q
```

`test_laya_compile_persist_restart_serve` compiles a Laya candidate from 200
observed text decisions, calibrates, shadows 60, evaluates to qualified, promotes
to ACTIVE, reopens the database, serves known states as `fast_path`, falls back on
a novel request and on a broken engine without changing the artifact checksum.
`test_laya_path_benchmark` asserts cold load, warm inference, artifact size, and
dispatcher overhead.

## Measurements (smoke test, this machine only)

`benchmarks/decision_jit.py` on arm64 macOS: cold compile, load, and first
inference 1.8 s; warm inference p50 33 ms, p95 49 ms (n=30); observe dispatch
p50 0.11 ms (n=100); artifact descriptor 886 bytes; checkpoint 846 MB;
process peak RSS ~1 GB. Not product benchmarks.

## Blockers recorded

- Boolean JSON states (`{"refund": true}`) are out of distribution; Laya answers
  `refund` for both values and never promotes. Text request states are required.
- The runtime clamps out-of-range checkpoint temperatures, so raw Laya scores are
  never treated as confidence; only held-out quality bounds count.
- `laya-mlx` was missing from `.venv` (Python 3.14); installed as
  `laya-mlx-0.2.0` with `mlx-0.32.2`. No `$LAYA_CHECKPOINT` set; tests resolve the
  Hugging Face cache automatically.
