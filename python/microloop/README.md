# Microloop Python SDK

Python bindings for the Microloop local trajectory engine.

```python
from microloop import Monitor, Policy, Runtime

# High-level usage
runtime = Runtime(run_id="run_101", replan=True, cooldown_steps=6)

event = {
    "schema_version": 1,
    "run_id": "run_101",
    "step": 1,
    "action": {"name": "shell", "fingerprint": "pytest_test_auth"},
    "observation": {
        "success": False,
        "fingerprint": "assertion_error_hash",
        "error_fingerprint": "AssertionError:auth.py:42"
    },
    "verification": {
        "scope": "pytest:auth",
        "observation_id": "obs_1",
        "failures": 4
    }
}

result = runtime.step(event)
print(result["decision"]["state"])       # "healthy", "warning", "stalled", "regressing"
print(result["intervention"]["kind"])    # "observe", "replan", "stop"
```

### Installation & Building

```sh
pip install maturin
maturin develop
```

No external credentials or network connections required. All trajectory evaluations run locally in Rust via PyO3.
