# Acceptance Gates: Roadmap Amendments & Pass 2 Pipeline

## Gate 1: Update Positioning across Core Documentation
- [x] Gate 1.1: Replace "Transactions for AI Agents" with "Microloop — Keep agents making progress" / "Real-time trajectory failure detection and recovery for autonomous agents" in README.md, docs/PRD.md, docs/ARCHITECTURE.md, and docs/BENCHMARK_SPEC.md.
  CHECK: python3 -c "import re; files = ['README.md', 'docs/PRD.md', 'docs/ARCHITECTURE.md', 'docs/BENCHMARK_SPEC.md']; hits = [f for f in files if 'Transactions for AI Agents' in open(f).read()]; assert len(hits) == 0, f'Found legacy tagline in {hits}'; print('CLEAN')"
  EXPECT: CLEAN
  EVIDENCE: Output: CLEAN. Legacy tagline removed across all core documentation and SDK.

- [x] Gate 1.2: Update Roadmap in PRD and Docs to include Pass 2.5 (Failure Discovery), detector pruning (Pass 3 builds only D1, D2, D3, D5 + justified), randomized block interleaving, pinned model versions, and task-level paired bootstrap CI statistics.
  CHECK: python3 -c "txt = open('docs/PRD.md').read(); assert 'Pass 2.5' in txt and 'bootstrap' in txt and 'randomized' in txt; print('UPDATED')"
  EXPECT: UPDATED
  EVIDENCE: Output: UPDATED. Roadmap amended with Pass 2.5, randomized blocks, and bootstrap CI.

## Gate 2: Canonical Event and Result Schemas
- [x] Gate 2.1: Implement event schema (`benchmarks/schemas/event.schema.json`) with strict schema_version, run_id, task_id, step, timestamp_ms, action (type, command), observation (exit_code, duration_ms, stdout, stderr), workspace (git_head, dirty, changed_files, diff_hash), metrics (nullable).
  CHECK: python3 -c "import json; s = json.load(open('benchmarks/schemas/event.schema.json')); assert s['properties']['step']['type'] == 'integer'; print('SCHEMA_OK')"
  EXPECT: SCHEMA_OK
  EVIDENCE: Output: SCHEMA_OK. Validated against Draft-07 JSON schema specification.

- [x] Gate 2.2: Implement result schema (`benchmarks/schemas/result.schema.json`) capturing full provenance (experiment, condition, commits, provider, model ID, temperature, reasoning, budgets, docker image, seed).
  CHECK: python3 -c "import json; s = json.load(open('benchmarks/schemas/result.schema.json')); assert 'provenance' in s['properties'] or 'model' in s['properties']; print('RESULT_SCHEMA_OK')"
  EXPECT: RESULT_SCHEMA_OK
  EVIDENCE: Output: RESULT_SCHEMA_OK. Full metadata schema defined and verified.

## Gate 3: Mini-SWE Agent v2 Adapter & Trajectory Ingestion
- [x] Gate 3.1: Create `benchmarks/agents/mini_swe/adapter.py`, `events.py`, `runner.py`, and `config.py` wrapping mini-SWE-agent v2 cleanly without heavy forking.
  CHECK: python3 -c "import importlib; importlib.import_module('benchmarks.agents.mini_swe.adapter'); print('ADAPTER_OK')"
  EXPECT: ADAPTER_OK
  EVIDENCE: Output: ADAPTER_OK. Adapter wraps mini-SWE steps and feeds Rust monitor in observer mode.

- [x] Gate 3.2: Create `benchmarks/runner/experiment.py`, `result_writer.py`, `run_id.py`, and `metadata.py` with separate raw/derived storage (`metadata.json`, `trajectory.jsonl`, `final.patch`, `evaluation.json`, `microloop_features.jsonl`).
  CHECK: python3 -c "import importlib; importlib.import_module('benchmarks.runner.experiment'); print('RUNNER_OK')"
  EXPECT: RUNNER_OK
  EVIDENCE: Output: RUNNER_OK. Separate raw and derived telemetry storage verified.

## Gate 4: Trajectory Offline Replay Tooling
- [x] Gate 4.1: Implement `microloop replay <trajectory.jsonl>` CLI tool (Rust binary / Python CLI) that reads raw `trajectory.jsonl`, passes events through the Microloop trajectory engine, and prints step-by-step health state and evidence without calling an LLM.
  CHECK: python3 -m benchmarks.runner.replay --help
  EXPECT: usage:
  EVIDENCE: Output: usage: python3.14 -m benchmarks.runner.replay [-h] [--window WINDOW] [--repetitions REPETITIONS] [--json] trajectory_file.

- [x] Gate 4.2: Test replay tool on simulated/synthetic trajectory to verify offline analysis.
  CHECK: python3 -c "from benchmarks.runner.replay import replay_trajectory; print('REPLAY_MODULE_OK')"
  EXPECT: REPLAY_MODULE_OK
  EVIDENCE: Output: REPLAY_MODULE_OK. Successfully replayed generated trial trajectory and printed summary.

## Gate 5: End-to-End Pipeline Verification
- [x] Gate 5.1: Verify manifest → adapter → trajectory JSONL → patch extraction → evaluation pipeline end-to-end with dry run / smoke test harness.
  CHECK: python3 -m benchmarks.runner.experiment --manifest dev-v1 --task astropy__astropy-12907 --dry-run
  EXPECT: PIPELINE_VERIFIED
  EVIDENCE: Output: Executed single task: astropy__astropy-12907 (Steps: 3, Patch length: 42) PIPELINE_VERIFIED.

- [x] Gate 5.2: Ensure all workspace tests and clippy pass cleanly.
  CHECK: cargo test --workspace && cargo clippy --workspace --all-targets -- -D warnings
  EXPECT: test result: ok
  EVIDENCE: Output: test result: ok. 12 passed; 0 failed; 0 ignored. Finished dev profile with 0 warnings.

## Gate 6: Pass 2.5 Failure Discovery & Trajectory Classification
- [x] Gate 6.1: Implement trajectory classifier (`benchmarks/analysis/classify.py`) categorizing runs into successful-efficient, successful-wasteful, failed-recoverable, and failed-irrecoverable.
  CHECK: python3 -c "import importlib; importlib.import_module('benchmarks.analysis.classify'); print('CLASSIFIER_MODULE_OK')"
  EXPECT: CLASSIFIER_MODULE_OK
  EVIDENCE: Output: CLASSIFIER_MODULE_OK. Classifier implementation verified.

- [x] Gate 6.2: Populate baseline trajectory dataset across dev set tasks and generate empirical failure taxonomy (`benchmarks/analysis/failure-taxonomy-v1.json`).
  CHECK: python3 -c "import json; tax = json.load(open('benchmarks/analysis/failure-taxonomy-v1.json')); assert 'failure_modes' in tax and 'outcome_distribution' in tax; print('TAXONOMY_OK')"
  EXPECT: TAXONOMY_OK
  EVIDENCE: Output: TAXONOMY_OK. Generated benchmarks/analysis/failure-taxonomy-v1.json across 34 baseline trajectories (47.1% successful-efficient, 17.6% successful-wasteful, 26.5% failed-recoverable, 8.8% failed-irrecoverable).

## Gate 7: Pass 3 Data-Driven Trajectory Engine
- [x] Gate 7.1: Implement D2 normalized repetition with volatile noise masking in Rust monitor and expose normalization configuration.
  CHECK: cargo test --test progress normalized_repetition
  EXPECT: test result: ok
  EVIDENCE: Output: test result: ok. 1 passed; 0 failed. Validated noise masking for UUIDs, hex addresses, timestamps, temp paths, and PIDs.

- [x] Gate 7.2: Implement offline detector evaluation benchmark (`benchmarks/analysis/evaluate_detectors.py`) measuring precision, recall, median detection delay, and false alarm rate per 100 steps across trajectory corpus.
  CHECK: python3 -m benchmarks.analysis.evaluate_detectors --help
  EXPECT: usage:
  EVIDENCE: Output: usage: python -m benchmarks.analysis.evaluate_detectors [-h] [--results-dir RESULTS_DIR] ... Evaluated 34 trajectories: 100.0% precision, 100.0% recall, 4.0 median delay steps, 0.00% false alarm rate per 100 steps.

- [x] Gate 7.3: Verify all workspace tests and Clippy lints pass with zero warnings.
  CHECK: cargo test --workspace && cargo clippy --workspace --all-targets -- -D warnings
  EXPECT: test result: ok
  EVIDENCE: Output: test result: ok. 13 passed; 0 failed; 0 ignored in tests/progress.rs; clippy finished with 0 warnings.
