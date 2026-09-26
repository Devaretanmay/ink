# Microloop: System Architecture & Technical Specification
## Version 0.3 — Real-Time Trajectory Failure Detection and Recovery

**Tagline:** *Microloop — Keep agents making progress.*  
**Internal Description:** *Real-time trajectory failure detection and recovery for autonomous agents.*  

---

## 1. Architectural Philosophy: Keep Agents Making Progress

Autonomous coding agents executing multi-step tasks across shells, file trees, and compilers frequently encounter non-progress states: repeating identical commands, oscillating between incompatible edits, hitting recurring test assertions, or thrashing tools without learning.

When a step fails today, typical harnesses merely append raw error traces to the model context. The context window balloons, the model grows confused, and the agent continues executing fruitless actions until the step or token budget is depleted.

**Microloop monitors the agent trajectory in real time**, evaluates objective environmental progress, and intervenes with structured recovery context before the trajectory becomes irrecoverable.

```
┌────────────────────────────────────────────────────────┐
│                      Agent Host                        │
│   (mini-SWE-agent v2 / Claude Code / OpenAI / Custom)  │
└───────────────────────────┬────────────────────────────┘
                            │ Action & Observation
┌───────────────────────────▼────────────────────────────┐
│                    MICROLOOP RUNTIME                   │
│  ┌──────────────────────────────────────────────────┐  │
│  │ Trajectory Engine (Fast Local Rust Core)         │  │
│  │  - Sliding Window Event Ring Buffer              │  │
│  │  - Repetition & Volatile-Field Normalization     │  │
│  │  - Error Recurrence Tracking                     │  │
│  │  - State Stagnation & Regression Detection       │  │
│  │  - Heuristic Progress Scoring [0.0, 1.0]         │  │
│  ├──────────────────────────────────────────────────┤  │
│  │ Policy State Machine                             │  │
│  │  - Observe (Telemetry, Replay & Logging)         │  │
│  │  - Replan (Structured Recovery Context)          │  │
│  │  - Stop (Budget Enforcement)                     │  │
│  └──────────────────────────────────────────────────┘  │
└───────────────────────────┬────────────────────────────┘
                            │ Recovery Signal
┌───────────────────────────▼────────────────────────────┐
│                  Host Agent Continues                  │
│        (Structured Evidence, Zero Cloud Exposure)      │
└────────────────────────────────────────────────────────┘
```

---

## 2. Core Engine Subsystems

The Microloop runtime consists of decoupled, memory-safe subsystems implemented in Rust:

### 2.1 Trajectory Engine
* **Sliding Window Memory:** Bounded ring buffer ($W \in [16, 64]$ actions) with $O(1)$ amortized insertion and bounded RAM usage.
* **Deterministic Detectors:**
  * *D1 Exact Repetition:* Identical tool calls producing identical output.
  * *D2 Normalized Repetition:* Equivalence after masking volatile noise (timestamps, UUIDs, PIDs, random IDs).
  * *D3 Error Recurrence:* Tracking normalized error signatures across intermediate exploratory actions.
  * *D5 State Stagnation:* Multiple steps executed without reduction in failing tests or improvement in verifier metrics.
  * *Subsequent Detectors (D4, D6, D7, D8):* Derived from empirical failures observed during Pass 2.5.
* **Progress Scorer:** Outputs `Healthy`, `Warning`, `Stalled`, or `Regressing` with evidence citations and a severity score $S \in [0.0, 1.0]$.

### 2.2 Policy Engine
* Decoupled from detection.
* Maps a `Decision` into a host-actionable `Intervention`:
  * `Observe`: Log and continue without intervention.
  * `Replan`: Inject structured factual evidence into the agent context with cooldown enforcement.
  * `Stop`: Terminate execution when configured step/token limits are reached.

### 2.3 Trajectory Replay Subsystem
* Operates offline on raw `trajectory.jsonl` files.
* Allows instant evaluation of new detector heuristics against historical runs without calling LLM APIs.

---

## 3. Canonical Event Data Model

Every agent interaction is captured as a versioned, machine-readable event:

```rust
pub struct Event {
    pub schema_version: u8,
    pub run_id: String,
    pub step: u64,
    pub action: Action,
    pub observation: Observation,
    pub verification: Option<Verification>,
    pub state_fingerprint: Option<String>,
}

pub struct Action {
    pub name: String,
    pub fingerprint: String,
}

pub struct Observation {
    pub success: Option<bool>,
    pub fingerprint: Option<String>,
    pub error_fingerprint: Option<String>,
}

pub struct Verification {
    pub scope: String,
    pub observation_id: String,
    pub failures: u64,
}
```

---

## 4. Separation of Raw and Derived Telemetry

To ensure reproducible science and zero-cost offline detector replay, every run generates structured files:

```text
results/run_abc/
├── metadata.json            # Run provenance (pinned model, provider, commit, seed)
├── trajectory.jsonl         # Raw step events emitted by the agent harness
├── final.patch              # Generated code patch
├── evaluation.json          # Official ground-truth test evaluation outcome
└── microloop_features.jsonl # Decisions and evidence computed by Microloop
```

---

## 5. Security & Offline Invariants

1. **100% In-Process & Offline:** Microloop does not open sockets, ping telemetry servers, or send data to external cloud APIs.
2. **Volatile Field Masking:** Sensitive tokens and environmental noise are scrubbed before computing fingerprints.
3. **No Prompt or Code Ingestion Required:** The trajectory engine operates over normalized action names, opaque hashes, exit codes, and numerical verifier metrics. The user's proprietary source code never leaves the host environment.
