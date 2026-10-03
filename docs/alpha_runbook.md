# Microloop Alpha Design Partner Runbook

This runbook guides technical integration leads, infrastructure engineers, and AI application developers deploying the **Microloop Decision JIT SDK (Alpha Release Candidate 0.6.0rc1)**.

---

## 1. Workload Selection Criteria

Microloop is designed for **bounded, repetitive, verifiable AI decision points**. Not all AI invocations are suitable.

### Ideal Decision Candidates
- **Discrete Categorical Outputs:** Classification, routing, tool/API selection, retry/escalation decisions, boolean approvals.
- **Bounded State Schemas:** 1–5 strongly typed fields (`string`, `integer`, `float`, `boolean`).
- **High Repetition:** Decision points executed at least 50–100 times per day with repeated states or semantic clustering.
- **Verifiable Outcomes:** Decisions whose correctness can be audited either deterministically (e.g. status codes, downstream success, rule checks) or via outcome telemetry.

### Unsuitable Workloads (Do NOT Use)
- **Open-Ended Text Generation:** Creative prose, summarization, conversational replies with infinite variation.
- **Unbounded State Payloads:** Raw megabyte blobs, arbitrary unstructured JSON, or states with continuously varying timestamps/UUIDs without canonical hashing.
- **High-Stakes One-Off Decisions:** Mission-critical financial or medical authorizations that occur infrequently and require full frontier LLM reasoning on every execution.

---

## 2. Model Provisioning & Weight Download Lifecycle

Microloop separates the lightweight Python runtime from the 421M internal learned decision model weights:

1. **Lightweight Package Install:**
   `pip install microloop` installs the core Decision Engine, SQLite storage layer, and execution runtime (only ~few megabytes). Model weights are **not** bundled into the distribution wheel.

2. **On-Demand Secure Provisioning:**
   On the first invocation of an internal learned model path (or when compiling a site with `engine="decision"`), Microloop automatically provisions the official pinned weights (`microloop-decision-v1`) into the local cache directory:
   ```text
   ~/.cache/microloop/models/decision-v1/
   ├── model.safetensors          (842.6 MB, float16 ModernBERT-large + DecisionHead)
   ├── rl_agent_config.json       (746 bytes)
   ├── encoder/config.json        (2.1 KB)
   ├── tokenizer/tokenizer.json   (3.58 MB)
   └── tokenizer/tokenizer_config.json (308 bytes)
   ```
   Total disk footprint: **~807 MB**.

3. **Ahead-of-Time Pre-Provisioning (Recommended for CI / Production Images):**
   To avoid any download latency or outbound network access at runtime, pre-install the model weights during Docker image building:
   ```bash
   microloop model-install
   ```
   Or programmatically in Python:
   ```python
   from microloop.internal.model.registry import install
   install()
   ```

4. **Cryptographic Checksum Verification:**
   Every model load verifies cryptographic SHA256 checksums against the immutable `checkpoint.json` specification. If any weight file is corrupted or tampered with, Microloop fails open to the host model fallback and raises `ValueError`.

5. **Offline & Air-Gapped Deployments:**
   For air-gapped environments without HuggingFace access:
   - Copy the verified `decision-v1` directory to a target path.
   - Set the environment variable:
     ```bash
     export MICROLOOP_MODEL_DIR=/opt/microloop/models/decision-v1
     ```
   - If model files are absent in an offline environment, Microloop raises an explicit `FileNotFoundError` with clear setup instructions, rather than silently degrading.

6. **Resource-Constrained Opt-Out:**
   On constrained instances (e.g. Lambda, tiny containers) where 807 MB disk or ~916 MB RAM is prohibitive, disable the learned model explicitly to run in exact-tier only mode:
   ```python
   loop = Microloop(model_enabled=False)  # Memory footprint: ~42.5 MB total process RSS
   ```
   Or set the environment variable:
   ```bash
   export MICROLOOP_MODEL_DISABLED=1
   ```

---

## 3. Four Operational Pilot Modes

Deploying Microloop with an external partner follows a 4-phase staged rollout. Each stage has explicit entry criteria and bounded blast radius.

```text
[Mode 1: OBSERVE] ──> [Mode 2: SHADOW] ──> [Mode 3: CONTROLLED ACTIVE]
        │                     │                          │
        └─────────────────────┴──────────────────────────┴──> [Mode 4: DISABLED] (Emergency)
```

### Mode 1: OBSERVE ONLY (Zero Risk Baseline)
- **Objective:** Collect baseline distributions, verify schema validation, measure repetition rates.
- **Fast Path Served:** **0%** (100% remote model fallback).
- **Application Blast Radius:** Zero. Application receives model output unchanged.
- **Configuration:**
  ```python
  from microloop import DecisionSite, Microloop, FallbackResult

  site = DecisionSite("support.route", {"dept": "string"}, ("billing", "tech", "sales"))
  loop = Microloop(path=".microloop/decisions.db", auto_maintenance=False)
  loop.register(site)

  res = loop.decide(
      site=site,
      state={"dept": request_dept},
      fallback=lambda: FallbackResult(call_remote_llm(), model_calls=1),
  )
  # Record verified outcome when known
  loop.record_outcome(res.decision_id, quality=1.0, verifier="crm_success", verifier_version="1.0")
  ```

### Mode 2: SHADOW EVALUATION (Offline Validation)
- **Objective:** Compile local candidate engine, generate shadow predictions alongside live traffic, evaluate accuracy against verified outcomes without serving.
- **Fast Path Served:** **0%** (100% remote model fallback).
- **Execution:**
  1. Compile candidate: `loop.compile(site, engine="exact")` (State transitions to `SHADOW`).
  2. Continue feeding live traffic for 50–100 decisions with outcomes.
  3. Evaluate candidate: `loop.maintenance(sites=[site], requirements=requirements)`

### Mode 3: CONTROLLED ACTIVE (Gradual Fast-Path Serving)
- **Objective:** Serve qualified local fast-path decisions (<1 ms latency, $0 model spend) while running continuous comparison verification (default 25% comparison rate).
- **Fast Path Served:** Up to 75% of qualified repeated states (25% reserved for live model comparison).
- **Safety Invariant:** If comparison reveals quality degradation exceeding `max_degradation`, Microloop automatically demotes the site back to `SHADOW` within 5–15 decisions.
- **Configuration:**
  ```python
  loop = Microloop(
      path=".microloop/decisions.db",
      auto_maintenance=True,
      maintenance_interval=30.0,
      maintenance_requirements=requirements,
  )
  ```

### Mode 4: EMERGENCY DISABLED (Instant Global Kill Switch)
- **Objective:** Immediately bypass Microloop entirely if operational anomalies occur.
- **Fast Path Served:** **0%**.
- **Storage / Lock Operations:** **Zero**. No SQLite database access, no lock acquisition.
- **Latency Overhead:** <10 microseconds (+8.9 μs p50 measured).
- **Trigger via Environment Variable (Recommended):**
  ```bash
  export MICROLOOP_DISABLED=1
  ```
- **Trigger via Code:**
  ```python
  loop = Microloop(disabled=True)
  ```

---

## 3. Observability, Logging, and Diagnostics

Microloop is quiet by default (`logging.NullHandler`). All events can be directed to standard logging or telemetry sinks.

### Standard Python Logging
Enable structured logs in your application:
```python
import logging

logging.basicConfig(level=logging.INFO)
logging.getLogger("microloop").setLevel(logging.INFO)
```

### Event Callback Hook (`on_event`)
Stream lifecycle transitions and runtime alerts to Datadog, Prometheus, OpenTelemetry, or Sentry:
```python
def handle_event(event_type: str, data: dict):
    # event_type: "lifecycle", "drift", "fail_open", "storage_warning"
    metrics_client.increment(f"microloop.{event_type}", tags=[f"site:{data.get('site')}"])

loop = Microloop(path=".microloop/decisions.db", on_event=handle_event)
```
*Note: Callback failures are completely isolated from your application control flow.*

### Programmatic Status Inspection
```python
status = loop.status("support.route")
print(f"State: {status['state']}")              # OBSERVE, SHADOW, or ACTIVE
print(f"Blocker: {status['blocker']}")          # Explains why site is not yet ACTIVE
print(f"Fast Served: {status['fast_served']}")  # Count of model calls avoided
```

### CLI Diagnostic Commands
Run from the terminal or container shell:
```bash
# Check status of all registered sites
python -m microloop status

# Inspect specific site details and promotion blockers
python -m microloop inspect support.route

# Show drift history and comparison agreement
python -m microloop drift support.route
```

---

## 4. Kill Switches & Safety Matrix

| Kill Switch Mechanism | Fast Path Served | Decision Storage | Overhead vs Direct LLM | Use Case |
| :--- | :--- | :--- | :--- | :--- |
| **Normal Active** | Qualified States (75%) | Enabled | Fast Path: 0.71 ms | Normal operations |
| **Soft Kill Switch**<br>`MICROLOOP_DISABLE_FAST_PATH=1`<br>or `Microloop(disable_fast_path=True)` | **0%** (100% Fallback) | Enabled (Records decisions & outcomes) | +130 μs | Suspected model drift; continue collecting telemetry for requalification |
| **Hard Kill Switch**<br>`MICROLOOP_DISABLED=1`<br>or `Microloop(disabled=True)` | **0%** (100% Fallback) | **Disabled** (Zero DB connections, `decision_id=None`) | +8.9 μs | Emergency bypass; zero storage or file locking overhead |

---

## 5. Storage Sizing & Retention Management

Microloop uses an embedded, single-node SQLite database with Write-Ahead Logging (WAL).

- **Disk Footprint:** Approximately **1.08 KB per decision** (including state payload and outcome evidence).
- **WAL Checkpointing:** Automatically executed during transactions and compaction.
- **Compaction Schedule:** Run weekly or daily during off-peak hours to prune raw decisions older than the retention window while preserving active site qualification:
  ```bash
  # Prune decisions older than 7 days and reclaim disk space
  python -m microloop compact --days 7
  ```
- **Programmatic Compaction:**
  ```python
  # Prune raw rows older than 7 days across all sites
  loop.compact(max_age_days=7, vacuum=True)
  ```

---

## 6. Incident Response & Troubleshooting Playbook

### Issue: "Site remains in SHADOW and will not promote to ACTIVE"
1. Run `python -m microloop inspect <site_name>` to view the exact `blocker` message.
2. Verify shadow traffic volume: Did the site receive at least `min_samples` fresh fallback decisions after compile?
3. Verify outcome reporting: Are outcomes recorded for shadow decisions? Missing outcomes block qualification to prevent bias.
4. For semantic engines: Ensure a valid callable `verifier` was supplied.

### Issue: "Site demoted from ACTIVE back to SHADOW"
1. This is the **correct safety behavior** when policy drift or distribution shift occurs.
2. Check drift checks: `python -m microloop drift <site_name>`.
3. If the demotion was valid: Allow the site to gather fresh shadow traffic under the new distribution, then run maintenance to requalify.
4. If the demotion was caused by a faulty verifier: Fix the verifier function in your integration.

### Issue: "Database contention or locking warnings in logs"
1. Microloop fail-open guarantees prevent host application crashes on storage contention. If a lock timeout occurs, Microloop immediately executes the fallback and returns `decision_id=None`.
2. Ensure database file is placed on a local SSD/NVMe volume, not a network-attached filesystem (NFS/EFS/SMB).
3. If running multi-process workers (e.g. Gunicorn/Uvicorn with 4+ workers), ensure each worker process initializes its own `Microloop` client instance.
