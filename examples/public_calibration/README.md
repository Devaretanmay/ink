# Public-data threshold calibration

Banking77 (PolyAI, CC-BY-4.0, license checked 2026-09-28) mapped to
`refund.next_action` choices via `mapping_v1.jsonl` (all 77 intents, no overlaps).
Public provenance only; never customer traffic. Local-only (laya-mlx, darwin arm64).

```bash
python examples/public_calibration/calibrate.py \
  --checkpoint <laya-mlx snapshot> --output .microloop/public-cal-v01/report.json
```

## Finding: provisional production gate infeasible

Target was accuracy lower bound 0.95 with degradation cap 0.01 at 95% confidence.
Measured on 1,500 calibration rows: refund 0.69, request_information 0.45,
specialist 0.29 lower bounds. Frozen gate from calibration evidence:
`min_quality` 0.29, `max_degradation` 0.01. Untouched evaluation split (3,080
rows) holds the frozen gate. Generic-instruction Laya does not separate these
intent groups at production level, so experimental thresholds stay in force and
release stays paused. Reaching the target needs a stronger engine, tighter
mapping, or task-specific evidence, none claimed here.
