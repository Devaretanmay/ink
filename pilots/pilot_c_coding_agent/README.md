# Pilot C: Autonomous Coding & CI Maintenance Agent

## Overview
Pilot C integrates Microloop into an autonomous repository maintenance and test-repair agent loop (inspired by modern coding agents). The agent receives test failures and workspace diffs, repeatedly selecting operational CI actions (`run_pytest`, `inspect_traceback`, `patch_ast`, `replan`, `commit_patch`).

## Discovery Audit
Running `microloop discover traces.jsonl` against raw developer loop traces identified 4 callsites:
- **`coding.action_dispatch`**: RECOMMENDED (`compile`). High repetition (96.2%), bounded 5 choices, verifier ready (100.0% coverage). Break-even in 274 decisions. Estimated annual savings: $2,414.34. Volatile field `build_uuid` flagged for developer review and excluded.
- **`coding.patch_synthesis`**: REJECTED (`ignore`). High output entropy (6.78 bits, 110 choices, avg length 150 chars). Free-form code synthesis is unbounded.
- **`coding.syntax_gate`**: REJECTED (`ignore`). Low repetition rate (14.1% < 15.0% threshold). Fast paths would rarely trigger.
- **`coding.commit_explainer`**: REJECTED (`investigate`). Sub-threshold observation depth (14 traces < 20).

## Verifier Design
- **Source**: Deterministic compiler and test runner execution (`pytest` exit code == 0).
- **Independence**: Outcome verified by real test execution, completely independent of model token probabilities.

## Economics & Latency
- **Baseline**: 200 decisions, $0.015 per cloud reasoning call, 1,200 ms p50 latency.
- **Microloop Fast Path**: 1.21 ms p50 latency (99.9% latency reduction).
- **Maintenance & Compaction**: Tested `client.maintenance(time_budget_sec=1.0)` and `client.compact(site, keep_recent=50, vacuum=True)` safely purging historical records without affecting active artifact validity.
