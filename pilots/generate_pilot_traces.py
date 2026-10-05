import json
import random
import time
from pathlib import Path


def generate_pilot_a(output_path: Path):
    random.seed(42)
    records = []
    base_time = time.time() - 86400 * 3

    tool_states = [
        {"intent": "find_file_definition", "file_type": "python", "step": 1},
        {"intent": "run_test_suite", "test_framework": "pytest", "step": 2},
        {"intent": "search_documentation", "query_type": "api_reference", "step": 1},
        {"intent": "inspect_diff_changes", "vcs": "git", "step": 3},
        {"intent": "clarify_ambiguous_spec", "confidence": "low", "step": 1},
        {"intent": "finalize_task_delivery", "tasks_completed": True, "step": 4},
    ]
    tool_map = {
        "find_file_definition": "read_file",
        "run_test_suite": "bash",
        "search_documentation": "web_search",
        "inspect_diff_changes": "bash",
        "clarify_ambiguous_spec": "ask_user",
        "finalize_task_delivery": "finish",
    }

    # 1. agent.tool_selector: 150 records, repetitive, bounded choices
    for i in range(150):
        st_proto = random.choice(tool_states)
        st = dict(st_proto)
        st["request_id"] = f"req_{i:05d}_{random.randint(1000, 9999)}"
        choice = tool_map[st_proto["intent"]]
        # 92% verifiable
        has_v = i % 12 != 0
        outcome = (
            {
                "quality": 1.0 if random.random() < 0.98 else 0.0,
                "verifier": "tool_execution",
                "verifier_version": "1",
                "evidence": {"exit_code": 0, "tool": choice},
            }
            if has_v
            else None
        )
        records.append(
            {
                "callsite": "agent.tool_selector",
                "state": st,
                "choice": choice,
                "latency_ms": round(random.uniform(180.0, 320.0), 2),
                "cost_usd": 0.0032,
                "timestamp": base_time + i * 120,
                "outcome": outcome,
            }
        )

    # 2. agent.continuation_gate: 80 records, binary choice
    for i in range(80):
        is_err = random.random() < 0.2
        choice = "await_user" if is_err else "continue"
        records.append(
            {
                "callsite": "agent.continuation_gate",
                "state": {"has_errors": is_err, "remaining_steps": random.randint(1, 5)},
                "choice": choice,
                "latency_ms": round(random.uniform(90.0, 160.0), 2),
                "cost_usd": 0.0012,
                "timestamp": base_time + i * 200,
                "outcome": {
                    "quality": 1.0,
                    "verifier": "session_integrity",
                    "verifier_version": "1",
                    "evidence": {"gate_valid": True},
                },
            }
        )

    # 3. agent.response_synthesis: 90 records, freeform text (unbounded)
    freeform_texts = [
        "I have located get_user_by_id in ink/user.py. Here is the implementation...",
        "Test suite failed with exit code 1. Assertion error at line 42 in test_auth.py...",
        "According to documentation, parameter 'timeout' expects milliseconds...",
        "Git diff indicates 4 files modified, 12 insertions, 3 deletions across src/engine.rs...",
        "Please confirm if the database connection string targets staging or local cluster?",
    ]
    for i in range(90):
        salt = random.randint(10000, 99999)
        choice_str = f"{random.choice(freeform_texts)} [Instance {i} payload {salt}]"
        records.append(
            {
                "callsite": "agent.response_synthesis",
                "state": {"query_id": f"q_{i}", "tokens": random.randint(50, 400)},
                "choice": choice_str,
                "latency_ms": round(random.uniform(800.0, 1600.0), 2),
                "cost_usd": 0.015,
                "timestamp": base_time + i * 250,
                "outcome": None,
            }
        )

    # 4. agent.subagent_dispatcher: 12 records (sub-threshold volume)
    for i in range(12):
        records.append(
            {
                "callsite": "agent.subagent_dispatcher",
                "state": {"task_class": f"rare_subtask_{i}"},
                "choice": "spawn_researcher",
                "latency_ms": 250.0,
                "cost_usd": 0.005,
                "timestamp": base_time + i * 1000,
                "outcome": {
                    "quality": 1.0,
                    "verifier": "spawn_status",
                    "verifier_version": "1",
                    "evidence": {"spawned": True},
                },
            }
        )

    random.shuffle(records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def generate_pilot_b(output_path: Path):
    random.seed(1337)
    records = []
    base_time = time.time() - 86400 * 4

    support_intents = [
        ("how do I change my billing credit card", "tier1_faq"),
        ("I was charged twice for monthly subscription please refund", "billing_refund"),
        ("cannot login error 500 server crash on checkout", "tech_escalation"),
        (
            "unrecognized login attempt from unknown IP address suspicious activity",
            "account_security",
        ),
        ("duplicate ticket please ignore previous email", "close_duplicate"),
        ("where is my order tracking number", "tier1_faq"),
        ("cancel order and issue full refund immediately", "billing_refund"),
        ("database timeout on API webhooks high latency", "tech_escalation"),
    ]

    # 1. support.triage_route: 200 records
    for i in range(200):
        text, route = random.choice(support_intents)
        st = {
            "ticket_text": text,
            "customer_tier": random.choice(["free", "pro", "enterprise"]),
            "session_id": f"sess_{random.randint(100000, 999999)}",
            "created_at_epoch": base_time + i * 150,
        }
        records.append(
            {
                "callsite": "support.triage_route",
                "state": st,
                "choice": route,
                "latency_ms": round(random.uniform(220.0, 380.0), 2),
                "cost_usd": 0.0028,
                "timestamp": base_time + i * 150,
                "outcome": {
                    "quality": 1.0 if random.random() < 0.97 else 0.0,
                    "verifier": "ticket_resolution_status",
                    "verifier_version": "1",
                    "evidence": {"resolved": True, "reopened": False},
                },
            }
        )

    # 2. support.urgency_tagger: 120 records
    for i in range(120):
        urgency = random.choice(["p0_urgent", "p1_high", "p2_normal", "p3_low"])
        records.append(
            {
                "callsite": "support.urgency_tagger",
                "state": {"severity": urgency[:2], "sla_hours": random.choice([1, 4, 24, 72])},
                "choice": urgency,
                "latency_ms": round(random.uniform(120.0, 200.0), 2),
                "cost_usd": 0.0015,
                "timestamp": base_time + i * 200,
                "outcome": {
                    "quality": 1.0,
                    "verifier": "sla_compliance",
                    "verifier_version": "1",
                    "evidence": {"compliant": True},
                },
            }
        )

    # 3. support.draft_reply: 95 records (free-form generation)
    replies = [
        "Dear customer, thank you for reaching out. We received your refund request...",
        "Hello! We apologize for the technical outage. Engineering deployed v2.4.1...",
        "Hi there, to update billing details, navigate to Settings > Billing...",
    ]
    for i in range(95):
        t_ref = random.randint(100000, 999999)
        choice_str = f"{random.choice(replies)} [Ticket #{t_ref} - Regards, Support]"
        records.append(
            {
                "callsite": "support.draft_reply",
                "state": {"ticket_id": f"t_{i}", "customer_name": f"User_{i}"},
                "choice": choice_str,
                "latency_ms": round(random.uniform(900.0, 2100.0), 2),
                "cost_usd": 0.018,
                "timestamp": base_time + i * 300,
                "outcome": None,
            }
        )

    # 4. support.sentiment_score: 15 records (sub-threshold)
    for i in range(15):
        records.append(
            {
                "callsite": "support.sentiment_score",
                "state": {"sample_id": i},
                "choice": f"sentiment_{random.choice(['pos', 'neg', 'neu'])}",
                "latency_ms": 110.0,
                "cost_usd": 0.001,
                "timestamp": base_time + i * 1000,
                "outcome": {
                    "quality": 1.0,
                    "verifier": "qa",
                    "verifier_version": "1",
                    "evidence": {"ok": True},
                },
            }
        )

    random.shuffle(records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def generate_pilot_c(output_path: Path):
    random.seed(999)
    records = []
    base_time = time.time() - 86400 * 5

    coding_scenarios = [
        ("AssertionError in test_payment_capture line 88", "inspect_traceback"),
        ("TypeError unsupported operand type int and NoneType in math_utils.py", "patch_ast"),
        ("SyntaxError unexpected EOF while parsing", "patch_ast"),
        ("All unit tests passing cleanly in test_engine.py", "commit_patch"),
        ("Multiple cyclic dependency errors across module imports", "replan"),
        ("Modified python/ink/discovery.py staged for verification", "run_pytest"),
    ]

    # 1. coding.action_dispatch: 160 records
    for i in range(160):
        diag, action = random.choice(coding_scenarios)
        st = {
            "failure_diagnosis": diag,
            "git_status": "dirty" if action != "commit_patch" else "clean_staged",
            "test_runner": "pytest",
            "build_uuid": f"uuid_{i:04d}_{random.randint(1000, 9999)}",
        }
        records.append(
            {
                "callsite": "coding.action_dispatch",
                "state": st,
                "choice": action,
                "latency_ms": round(random.uniform(950.0, 1450.0), 2),
                "cost_usd": 0.015,
                "timestamp": base_time + i * 180,
                "outcome": {
                    "quality": 1.0 if random.random() < 0.98 else 0.0,
                    "verifier": "pytest_exit_code",
                    "verifier_version": "1",
                    "evidence": {"exit_code": 0, "runner": "pytest"},
                },
            }
        )

    # 2. coding.syntax_gate: 85 records
    for i in range(85):
        ext = random.choice(["py", "rs", "json", "md"])
        choice = "run_syntax_check" if ext in ("py", "rs") else "skip_syntax_check"
        records.append(
            {
                "callsite": "coding.syntax_gate",
                "state": {"file_extension": ext, "lines_changed": random.randint(1, 100)},
                "choice": choice,
                "latency_ms": round(random.uniform(150.0, 280.0), 2),
                "cost_usd": 0.0025,
                "timestamp": base_time + i * 300,
                "outcome": {
                    "quality": 1.0,
                    "verifier": "ast_parse_verifier",
                    "verifier_version": "1",
                    "evidence": {"valid": True},
                },
            }
        )

    # 3. coding.patch_synthesis: 110 records (unbounded code synthesis)
    patches = [
        "diff --git a/src/lib.rs b/src/lib.rs\n@@ -10,3 +10,4 @@ pub fn add...",
        "def compute_hash(val):\n    import hashlib\n    return hashlib.sha256(val).hexdigest()\n",
        "if not isinstance(token, str):\n    raise TypeError('str token required')\nreturn True\n",
    ]
    for i in range(110):
        records.append(
            {
                "callsite": "coding.patch_synthesis",
                "state": {"file_path": f"src/module_{i % 5}.py", "bug_id": f"BUG_{i}"},
                "choice": f"{random.choice(patches)} # Var {i}",
                "latency_ms": round(random.uniform(1500.0, 3200.0), 2),
                "cost_usd": 0.035,
                "timestamp": base_time + i * 350,
                "outcome": None,
            }
        )

    # 4. coding.commit_explainer: 14 records (sub-threshold)
    for i in range(14):
        records.append(
            {
                "callsite": "coding.commit_explainer",
                "state": {"commit_sha": f"sha_{i:04d}"},
                "choice": "Refactored module dependencies to eliminate cyclic import loop",
                "latency_ms": 700.0,
                "cost_usd": 0.010,
                "timestamp": base_time + i * 2000,
                "outcome": None,
            }
        )

    random.shuffle(records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


if __name__ == "__main__":
    generate_pilot_a(Path("pilots/pilot_a_orchestrator/traces.jsonl"))
    generate_pilot_b(Path("pilots/pilot_b_support_routing/traces.jsonl"))
    generate_pilot_c(Path("pilots/pilot_c_coding_agent/traces.jsonl"))
    print("Pilot trace generation complete.")
