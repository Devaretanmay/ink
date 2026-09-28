# Laya fast path: feasibility, spike, measurements

## Feasibility

- Package: `laya-mlx==0.2.0`, optional dep for darwin arm64, Python >= 3.11, macOS 14+.
  Verified under the supported Python range (`requires-python >=3.10, <3.14`).
- Interface: `laya_mlx.load(checkpoint)` plus `agent.predict(state, questions)`.
  `Microloop` binds it via `LayaEngine` (`python/microloop/microloop/internal/engines.py`).
- Artifact: `model.safetensors` (~846 MB) plus `rl_agent_config.json`,
  `encoder/config.json`, tokenizer files, `mlx_config.json`. Historical spike note
  (this machine only): `models--aac6fef--laya-mlx` snapshot `20aed815`.
  Runs require an explicit local `--checkpoint` path; nothing downloads weights.
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

## Measurements (smoke test, arm64 macOS, 2026-09-28)

`benchmarks/decision_jit.py`: cold compile, load, and first
inference 1.8 s; warm inference p50 33 ms, p95 49 ms (n=30); observe dispatch
p50 0.11 ms (n=100); artifact descriptor 886 bytes; checkpoint 846 MB;
process peak RSS ~1 GB. Dated committed evidence may differ slightly
([laya-benchmark.json](evidence/laya-benchmark.json)); both are smoke figures,
not product benchmarks.

## Blockers recorded

- An earlier boolean-state configuration did not distinguish both choices.
  This is a measured configuration limitation, not proof that all structured
  states are unsupported. The latest refund run qualified one structured state.
- The runtime clamps out-of-range checkpoint temperatures, so raw Laya scores are
  never treated as confidence; only held-out quality bounds count.
- `laya-mlx` installs only inside the supported Python range. Runs take an
  explicit local checkpoint path; tests accept `$LAYA_CHECKPOINT` or fall back
  to the local Hugging Face cache.

## Current combined experiment

The real local Qwen→Laya refund run completed all lifecycle gates. See
[validation](validation-v0.4.md) and [benchmark evidence](evidence/laya-benchmark.json)
for current measurements. The benchmark records process peak RSS as well as cold
load, warm inference, descriptor size, checkpoint size, and dispatcher overhead.
A timing ordering (warm < cold) is not asserted as a correctness test.
