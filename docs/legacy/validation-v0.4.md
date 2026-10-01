> Historical local-model run below used the external runtime before integration.
> Current Microloop Decision v1 ships the inference source directly. See
> `integrated-model-v0.4.md` for fresh-wheel validation of the bundled runtime.

# Microloop v0.4 validation

## Verified local model workload

[Machine-readable evidence](../evidence/local-model-laya.json) records a complete run
of **7,209 generated operational cases** with actual local model inference:

- Original fallback: Qwen2.5-Coder-3B-Instruct-4bit through MLX-LM.
- Fast path: Laya-MLX 0.2.0 with the existing local checkpoint.
- One real bounded generation per fallback; attention KV prefixes reused, outputs
  never cached or replaced with fixture answers.
- Independent SQLite action ledger verifies executed and replayed actions.

| Phase | Decisions | Fast path | Original model | Outcome quality |
|---|---:|---:|---:|---:|
| Observe | 3,000 | 0 | 3,000 | 68.33% |
| Shadow | 600 | 0 | 600 | 68.33% |
| Active | 1,800 | 405 | 1,395 | 90.83% |
| Novel | 6 | 0 | 6 | 100% |
| Deliberate drift | 1,800 | 385 | 1,415 | 31.67% |
| After demotion | 3 | 0 | 3 | 33.33% |

All seven lifecycle acceptance checks passed. The steady comparison retained the
active path. Drift demoted it to SHADOW; subsequent choices used the original
model. The original model itself performs poorly after drift, so demotion does
not imply the application is correct—it restores the original decision path.

**405 verified model calls avoided** in the steady phase (22.5% of its decisions).
790 total calls bypassed includes 385 unsuccessful choices during deliberately
introduced drift; those are excluded from the verified metric. Total measured
fallback calls: 6,419. Token usage: 664,313 logical input tokens (including KV-cached
prefixes), 6,419 output tokens. Cost is unknown, not zero.

The candidate qualified for one exact state and improved a weak original model's
choice there. This is a functional proof on generated data, not evidence of
production coverage or generalization. Maintenance ran at explicit phase
boundaries; detection was not instantaneous. No latency improvement is claimed.
The full run took 729.79 seconds on this machine.

## Reproduction

Install the checkout and optional local packages, then use existing checkpoint
paths. The runtime never downloads model weights automatically.

```bash
uv pip install 'laya-mlx==0.2.0' 'mlx-lm==0.31.3'
python -m examples.refund_agent.agent --output .microloop/my-real-run \
  --engine laya --checkpoint /absolute/laya/checkpoint \
  --local-model /absolute/qwen/checkpoint --require-lifecycle
```

Raw local evidence remained in a git-ignored `.microloop/final-real-local-laya/`
on the run machine only (not committed); the committed summary plus SHA-256 is
the auditable record. Fresh output directories are required.

The default fixture run remains useful for offline CI:

```bash
python -m examples.refund_agent.agent --output .microloop/my-offline-run --require-lifecycle
```

It records zero model calls and cannot establish model savings. CI now fails when
the requested lifecycle does not complete; it does not silently accept a report
that stayed in shadow.

## Automated checks

[Final release-check record](../evidence/release-checks.json): **150 Python tests passed
against a fresh installed wheel as of the recorded commit therein**, including real Laya tests (zero skips). **95
Rust tests passed**; formatting, Clippy, lint, legacy examples, installed CLI,
SQLite integrity/foreign-key checks, and all installed-demo lifecycle gates passed.
The wheel SHA-256 is recorded for exact reproducibility. The installed offline
demo served 397/600 active decisions with unchanged outcome quality.

The Rust suite, formatting, Clippy, Python lint, compatibility examples, new
decision tests, and wheel build are exercised by `make check`. Separate clean-wheel
validation runs the installed entry point and suite. Optional real Laya tests check
compile, calibration, promotion, restart, local service, novelty and engine-failure
fallback. Migration tests preserve outcomes and repair the earlier broken foreign
key; incomplete shadow evidence and changed verifiers cannot qualify a path.

See [the 60-subtask audit](implementation-status-v0.4.md) for the implementation map.

## Local inference measurements

[Benchmark evidence](../evidence/laya-benchmark.json): 30 warm samples, median 46.41 ms,
p95 48.67 ms. Compile, integrity hashing, load, and first inference together:
1.83 s. Observe-only dispatcher median: 61.75 µs. Checkpoint files: 846.20 MB;
process peak RSS: 982.19 MB. These are smoke measurements on macOS arm64, not
performance guarantees or an end-to-end latency comparison.

```bash
python benchmarks/decision_jit.py --checkpoint /absolute/laya/checkpoint \
  --output .microloop/laya-benchmark.json
```

## Public-data threshold calibration

Banking77 (PolyAI, CC-BY-4.0, license checked 2026-09-28) mapped to refund
choices (`examples/public_calibration/mapping_v1.jsonl`, all 77 intents, public
provenance only, never customer traffic). Provisional production gate (0.95
accuracy lower bound, 0.01 degradation cap, 95% confidence) is infeasible with
generic-instruction Laya: calibration lower bounds 0.29-0.69 on 1,500 rows;
frozen weak gate held on 3,080 untouched rows. Experimental thresholds stay in
force.

## External validation still unavailable

No cloud-provider credential is configured. The remote `--model` path therefore
has no live frontier-provider validation here. Production/customer traffic is also
unavailable. Neither is substituted with local or fixture claims. The real local
model experiment above closes the model-execution proof; cloud and customer
validation remain external follow-up gates, not verified results.
