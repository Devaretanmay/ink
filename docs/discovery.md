# Discovery and Trace Format

`ink discover` is the front door for evaluating whether Ink is right for your workload before writing any integration code.

---

## Quickstart

```bash
ink discover traces.jsonl
```

Add `--profile` for deep economic metrics (entropy, stability, qualification costs).
Add `--snippet` to generate ready-to-use Python integration code.

### With ROI Simulation

```bash
ink discover traces.jsonl \
  --monthly-volume 120000 \
  --avg-call-cost 0.0025 \
  --avg-call-latency-ms 180
```

---

## Canonical Trace Format

The simplest format is a JSON Lines (`.jsonl`) file where each line represents one decision:

```json
{
  "callsite": "support.route",
  "state": {"text": "Item damaged in shipping", "amount": 25},
  "choice": "refund",
  "choices": ["refund", "request_info", "specialist"],
  "cost_usd": 0.002,
  "latency_ms": 180,
  "model": "gpt-4o-mini",
  "outcome": {"quality": 1.0, "verifier": "fulfillment_system"},
  "timestamp": 1727712000
}
```

### Required Fields

| Field | Type | Description |
| :--- | :--- | :--- |
| `callsite` | `string` | Identifier for the decision site (e.g. `support.route`, `tool_select`). Also accepted: `site_name`, `template_name`. |
| `choice` | `string` | The decision made by the model. Also accepted: `response`, `action`. |

### Recommended Fields

| Field | Type | Description |
| :--- | :--- | :--- |
| `state` | `dict` | The inputs the decision was based on. Also accepted: `query`, `input`, `prompt`, `arguments`. |
| `choices` | `list[str]` | The allowed choices for this decision site. |
| `outcome` | `dict` or `bool` | Verification outcome: `{"quality": 1.0, "verifier": "..."}` or `true`/`false`. |
| `cost_usd` | `float` | Estimated cost of the model call. Defaults to $0.0005 if omitted. |
| `latency_ms` | `float` | Duration of the model call in milliseconds. Defaults to 150 ms if omitted. |
| `model` | `string` | The model that made the decision (e.g. `gpt-4o-mini`, `claude-3-5-sonnet`). |
| `timestamp` | `float` | Unix timestamp of the decision. |

---

## Supported Input Formats

Ink natively parses traces from common observability systems:

### 1. OpenTelemetry Spans

Standard GenAI semantic convention spans:

```json
{
  "name": "gen_ai.chat",
  "attributes": {
    "gen_ai.operation.name": "support.route",
    "gen_ai.prompt": "Item damaged in shipping",
    "gen_ai.completion": "refund",
    "gen_ai.request.model": "gpt-4o-mini",
    "llm.cost": 0.002
  },
  "start_time_unix_nano": 1727712000000000000,
  "end_time_unix_nano": 1727712000180000000,
  "status": {"code": 1}
}
```

### 2. LangSmith Runs

Exported runs from LangSmith / LangChain / LangGraph:

```json
{
  "name": "support.route",
  "run_type": "llm",
  "inputs": {"text": "Item damaged in shipping", "amount": 25},
  "outputs": {"choice": "refund"},
  "start_time": 1727712000.0,
  "end_time": 1727712000.18,
  "extra": {"metadata": {"model": "gpt-4o-mini", "total_cost": 0.002}},
  "feedback": [{"score": 1.0}]
}
```

### 3. LiteLLM Logs

Standard spend/call logs from LiteLLM proxy:

```json
{
  "litellm_call_id": "call_123",
  "model": "gpt-4o-mini",
  "messages": [{"role": "user", "content": "Item damaged in shipping"}],
  "response": {"choices": [{"message": {"content": "refund"}}]},
  "response_cost": 0.002,
  "response_time_ms": 180,
  "status": "success",
  "metadata": {"callsite": "support.route"}
}
```

---

## Converting Custom Logs

If your logs are in a custom format, a simple Python script can convert them to Ink's canonical format:

```python
import json

def convert_logs(input_file: str, output_file: str):
    """Convert application logs to Ink canonical trace format."""
    with open(input_file) as f_in, open(output_file, "w") as f_out:
        for line in f_in:
            if not line.strip():
                continue
            entry = json.loads(line)

            trace = {
                "callsite": entry.get("operation") or entry.get("endpoint"),
                "state": {
                    "text": entry.get("user_query", ""),
                    "category": entry.get("category", ""),
                },
                "choice": entry.get("model_decision") or entry.get("action_taken"),
                "choices": ["escalate", "resolve", "refund"],
                "cost_usd": entry.get("cost", 0.002),
                "latency_ms": entry.get("duration_ms", 150),
                "model": entry.get("model", "unknown"),
                "outcome": {
                    "quality": 1.0 if entry.get("success") else 0.0,
                    "verifier": "order_db",
                } if "success" in entry else None,
            }

            f_out.write(json.dumps(trace) + "\n")

if __name__ == "__main__":
    convert_logs("app_logs.jsonl", "ink_traces.jsonl")
```

---

## How Candidates Are Classified

| Recommendation | Criteria | Meaning |
| :--- | :--- | :--- |
| **STRONG CANDIDATE** (`compile`) | Repetition ≥ 15%, choices ≤ 15, entropy ≤ 3.8, verifier coverage ≥ 20%, positive ROI | High-confidence target for Ink Fast Path compilation. |
| **INVESTIGATE** | Low sample count (< 20 calls), no verifier detected, or marginal economic return | Potential candidate; collect more traces or identify outcome verification signal. |
| **IGNORE** | High entropy (> 3.8 bits), > 15 choices, avg choice length > 80 chars, or repetition < 15% | Not bounded or not repetitive. Do not attempt compilation. |
