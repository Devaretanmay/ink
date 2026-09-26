# Microloop

### Microloop — Keep agents making progress.
*Real-time trajectory failure detection and recovery for autonomous agents.*

---

[![CI](https://github.com/Devaretanmay/microloop/actions/workflows/ci.yml/badge.svg)](https://github.com/Devaretanmay/microloop/actions)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Status: Experiment 001](https://img.shields.io/badge/Status-Experiment_001_Active-orange.svg)](docs/PRD.md)

Autonomous AI agents frequently get stuck in unproductive loops, repeat identical errors, thrash tools without gaining new information, or silently regress on test progress. 

**Microloop detects when autonomous agents stop making useful progress and helps them recover.**

It monitors execution trajectories in real time with an ultra-fast, 100% offline Rust engine. When non-progress is detected, it delivers structured recovery signals so the agent can course-correct before burning out its token budget or failing the task.

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
│  │  - Repetition & Masked Normalization             │  │
│  │  - Error Recurrence Tracking                     │  │
│  │  - State Stagnation & Regression Detection       │  │
│  │  - Progress Scoring & Heuristic Severity         │  │
│  ├──────────────────────────────────────────────────┤  │
│  │ Policy Engine                                    │  │
│  │  - Observe (Telemetry & Diagnostics)             │  │
│  │  - Replan (Structured Context Injection)         │  │
│  │  - Stop (Budget Governance)                      │  │
│  └──────────────────────────────────────────────────┘  │
└───────────────────────────┬────────────────────────────┘
                            │ Recovery Signal
┌───────────────────────────▼────────────────────────────┐
│                  Host Agent Continues                  │
│       (Informed Course-Correction, Zero Cloud Leak)    │
└────────────────────────────────────────────────────────┘
```

---

## Active Milestone: Experiment 001 (Validation Benchmark v1)

We believe claims about agent reliability must be proved with empirical, reproducible benchmarks. The current engineering milestone is **Experiment 001**: testing whether local trajectory monitoring and structured recovery improve end-to-end task completion without altering the base model, prompt, or tool harness.

* **Target Benchmarks:** [SWE-bench Verified](https://www.swebench.com/) (real GitHub software engineering issues) and [Terminal-Bench](https://github.com/jvpoulos/terminal-bench) (system and CLI workflows).
* **3-Stage Validation Ladder:**
  1. *Microloop Dev:* 30 frozen tasks $\times$ 3 seeds $\times$ 4 conditions = 360 runs per model.
  2. *Microloop Validation:* 100 disjoint held-out tasks $\times$ 3 seeds $\times$ 4 conditions = 1,200 runs per model.
  3. *Microloop Benchmark:* 300–500 tasks across the full SWE-bench Verified distribution.
* **4 Experimental Conditions:**
  * **Condition A (Vanilla):** Base agent with no trajectory monitoring.
  * **Condition B (Retry Baseline):** Base agent with naive single retry/restart policy.
  * **Condition C (LLM Supervisor):** Base agent supervised by a separate LLM prompt every $N$ steps.
  * **Condition D (Microloop):** Identical base agent with Microloop trajectory monitoring and recovery feedback.

### The 8 Trajectory Detectors (v1)
Microloop evaluates 8 deterministic signals locally in Rust:
1. **Exact Repetition:** Identical tool calls and outputs recurring in the sliding window.
2. **Normalized Repetition:** Repetition detected after masking noise (timestamps, UUIDs, PIDs, paths).
3. **Error Recurrence:** Tracking recurring error signatures across non-consecutive steps.
4. **State Oscillation:** Detecting cyclical state changes ($A \to B \to A \to B$).
5. **State Stagnation:** Monitoring active tool execution while objective progress metrics remain stagnant.
6. **State Regression:** Detecting when agent actions worsen verified progress (e.g., failure counts increase).
7. **Tool Thrashing:** High tool frequency with collapsing information gain and zero mutations.
8. **Failure Cascade:** Pinpointing the first unresolved root failure triggering downstream errors.

---

## Quickstart

### Rust Engine

```rust
use microloop::monitor::{Action, Event, Monitor, MonitorConfig, Observation, Verification};
use microloop::policy::{InterventionKind, Policy, PolicyConfig};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut monitor = Monitor::new("run_django_101".into(), MonitorConfig::default())?;
    let mut policy = Policy::new(PolicyConfig {
        replan: true,
        cooldown_steps: 4,
        max_replans: 2,
        stop_at_step: Some(50),
    })?;

    let event = Event {
        schema_version: 1,
        run_id: "run_django_101".into(),
        step: 1,
        action: Action {
            name: "shell".into(),
            fingerprint: "pytest_auth_test".into(),
        },
        observation: Observation {
            success: Some(false),
            fingerprint: Some("err_sig_hash".into()),
            error_fingerprint: Some("AssertionError:auth.py:42".into()),
        },
        verification: Some(Verification {
            scope: "pytest:auth".into(),
            observation_id: "obs_1".into(),
            failures: 3,
        }),
        state_fingerprint: None,
    };

    let decision = monitor.observe(event)?;
    let intervention = policy.apply(&decision)?;

    if intervention.kind == InterventionKind::Replan {
        println!("Recovery signal: {}", intervention.feedback.unwrap());
    }
    Ok(())
}
```

### Python SDK

```python
from microloop import Runtime

runtime = Runtime(run_id="run_django_101", replan=True, cooldown_steps=4)

result = runtime.step({
    "schema_version": 1,
    "run_id": "run_django_101",
    "step": 1,
    "action": {"name": "shell", "fingerprint": "pytest_auth_test"},
    "observation": {
        "success": False,
        "fingerprint": "err_hash",
        "error_fingerprint": "AssertionError:auth.py:42"
    },
    "verification": {
        "scope": "pytest:auth",
        "observation_id": "obs_1",
        "failures": 3
    }
})

print(result["decision"]["state"])     # "healthy", "warning", "stalled", "regressing"
print(result["intervention"]["kind"])  # "observe", "replan", "stop"
```

---

## Offline Privacy & In-Process Execution

* **100% In-Process:** Microloop runs entirely inside your host process via native Rust or Python (PyO3).
* **Zero Network Traffic:** The core monitor has no network dependencies and sends zero data to external servers.
* **No Raw Prompts or Proprietary Code Ingested:** Microloop operates over normalized action names, opaque content hashes, exit codes, and numerical verifier metrics. Sensitive source code and user prompts remain private to the host agent.

---

## Documentation

* [Product Requirements Document (PRD)](docs/PRD.md)
* [System Architecture & Transaction Specification](docs/ARCHITECTURE.md)
* [Validation Benchmark Protocol (Experiment 001)](docs/BENCHMARK_SPEC.md)
* [Repository Scope & Boundaries](docs/REPOSITORY-SCOPE.md)
* [Contributing Guidelines](CONTRIBUTING.md)

---

## License

Apache-2.0. See [LICENSE](LICENSE) for details.
