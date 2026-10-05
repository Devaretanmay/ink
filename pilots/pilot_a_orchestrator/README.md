# Pilot A: Autonomous Agent Tool Orchestration

## Overview
Pilot A integrates Ink into an autonomous agent orchestration loop. In agent systems, the execution controller repeatedly assesses scratchpad state and task context to select the next tool action (`read_file`, `bash`, `web_search`, `ask_user`, `finish`).

## Discovery Audit
Running `ink discover traces.jsonl` against raw agent telemetry identified 4 callsites:
- **`agent.tool_selector`**: RECOMMENDED (`compile`). High repetition (96.0%), bounded 5 choices, verifier ready (91.3% coverage). High-cardinality volatile field `request_id` flagged for explicit developer review and excluded from state contract.
- **`agent.continuation_gate`**: RECOMMENDED (`compile`). Binary decision (`continue`, `await_user`), 88.8% repetition, verifier ready.
- **`agent.response_synthesis`**: REJECTED (`ignore`). High output entropy (6.49 bits, 90 choices, avg length 155 chars). Free-form natural language generation is unbounded.
- **`agent.subagent_dispatcher`**: REJECTED (`investigate`). Insufficient observation count (12 traces < 20 minimum).

## Verifier Design
- **Source**: Downstream tool execution exit status & receipt (`exit_code: 0`, `tool: <choice>`).
- **Timing**: Delayed verification. The outcome is recorded after the tool executes, confirming whether the dispatched tool ran successfully.
- **Missing Outcomes**: Partial completion tested on 10% of sessions where users interrupted the agent loop.

## Integration
- **LOC Added**: 18 lines around `ml.decide(...)` and `ml.record_outcome(...)`.
- **Files Modified**: 1 file (`integrated_agent.py`).
- **State Redaction**: `request_id` excluded explicitly based on discovery recommendation.
