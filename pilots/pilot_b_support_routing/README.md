# Pilot B: Enterprise Customer Support & Workflow Routing

## Overview
Pilot B integrates Microloop into a multi-tier customer support ticket triage and routing service. In support workloads, incoming tickets repeatedly exhibit common intent clusters (billing refunds, credential resets, technical errors, spam/duplicates).

## Discovery Audit
Running `microloop discover traces.jsonl` against raw support traces identified 4 callsites:
- **`support.triage_route`**: RECOMMENDED (`compile`). High repetition (88.0%), 5 bounded choices, verifier ready (100.0% coverage), break-even in 300 decisions. Volatile fields `created_at_epoch` and `session_id` flagged for developer review and excluded from state.
- **`support.urgency_tagger`**: RECOMMENDED (`compile`). 86.7% repetition, 4 choices, verifier ready.
- **`support.draft_reply`**: REJECTED (`ignore`). High output entropy (6.57 bits, 95 choices, avg length 168 chars). Unbounded email body text generation.
- **`support.sentiment_score`**: REJECTED (`investigate`). Sub-threshold observation depth (15 traces < 20).

## Verifier Design
- **Source**: Factual ticket resolution signal (`status: resolved`, `reopened: False`).
- **Timing**: Immediate ticket lifecycle resolution receipt.
- **Teacher-as-Verifier Shortcut Avoided**: Correctness is grounded strictly in whether the customer issue was resolved without reopen, not whether Microloop matched the cloud model's text.

## Policy Drift & Explicit Invalidation Experiment
We tested both passive drift detection and explicit policy invalidation:
1. **Passive Drift**: Injected a policy change where free-tier refund requests must route to `account_security` due to fraud risk. As comparison traffic encountered disagreements, `client.reevaluate` detected degraded lower bounds and automatically demoted the artifact back to `SHADOW`.
2. **Explicit Invalidation**: Tested `client.invalidate(site, reason="fraud_policy_v2_migration")`. Demoted the artifact to `SHADOW` instantaneously with zero post-migration corrupted serves.
