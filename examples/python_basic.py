"""
Microloop Python Example
Demonstrating trajectory monitoring and policy intervention.
"""
from microloop import Monitor, Policy, Runtime

def main():
    print("=== Microloop Python Trajectory Monitoring ===")
    runtime = Runtime(run_id="demo_task_42", replan=True, cooldown_steps=4)

    # Simulated trajectory where an agent gets stuck in an error loop
    for step in range(1, 6):
        event = {
            "schema_version": 1,
            "run_id": "demo_task_42",
            "step": step,
            "action": {
                "name": "shell",
                "fingerprint": f"modify_auth_v{step}"
            },
            "observation": {
                "success": False,
                "fingerprint": "output_assertion_fail",
                "error_fingerprint": "AssertionError:test_token:55"
            },
            "verification": {
                "scope": "pytest:auth",
                "observation_id": f"obs_{step}",
                "failures": 3
            }
        }

        result = runtime.step(event)
        decision = result["decision"]
        intervention = result["intervention"]

        print(f"Step {step}: State={decision['state']}, Intervention={intervention['kind']}")

        if intervention["kind"] == "replan":
            print("\n[RECOVERY SIGNAL INJECTED TO AGENT]")
            print(intervention.get("feedback", ""))
            print("-" * 50)

if __name__ == "__main__":
    main()
