"""Workload definitions, realistic traffic generation, and temporal splits.

Provides 4 distinct workloads:
  1. support: Support Ticket Action Routing (5,000 items)
  2. tool_select: Agent Tool Selection (5,000 items)
  3. incident_triage: Incident Triage Escalation (5,000 items)
  4. research_novelty: High-Entropy Open Web Research [Negative Control] (3,000 items)
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable


@dataclass(frozen=True)
class WorkloadConfig:
    name: str
    description: str
    choices: tuple[str, ...]
    decision_site_share: float
    total_decisions: int
    history_count: int
    eval_count: int
    teacher_in_cost_per_m: float
    teacher_out_cost_per_m: float
    teacher_latency_base_ms: float
    cheap_in_cost_per_m: float
    cheap_out_cost_per_m: float
    cheap_latency_base_ms: float
    severity_matrix: dict[str, float]
    is_negative_control: bool = False


WORKLOAD_CONFIGS: dict[str, WorkloadConfig] = {
    "support": WorkloadConfig(
        name="support",
        description="Customer service triage & resolution action",
        choices=("refund", "request_info", "specialist"),
        decision_site_share=0.20,  # 1 out of 5 LLM calls in ticket resolution workflow
        total_decisions=5000,
        history_count=3500,
        eval_count=1500,
        teacher_in_cost_per_m=2.50,
        teacher_out_cost_per_m=10.00,
        teacher_latency_base_ms=125.0,
        cheap_in_cost_per_m=0.15,
        cheap_out_cost_per_m=0.60,
        cheap_latency_base_ms=32.0,
        severity_matrix={"request_info": 1.0, "specialist": 2.0, "refund": 5.0, "default": 1.0},
    ),
    "tool_select": WorkloadConfig(
        name="tool_select",
        description="Autonomous coding & ops agent tool invocation decision",
        choices=("search_docs", "database_lookup", "ask_user", "finish"),
        decision_site_share=0.25,  # 2 out of 8 LLM calls in autonomous loop
        total_decisions=5000,
        history_count=3500,
        eval_count=1500,
        teacher_in_cost_per_m=3.00,
        teacher_out_cost_per_m=12.00,
        teacher_latency_base_ms=145.0,
        cheap_in_cost_per_m=0.20,
        cheap_out_cost_per_m=0.80,
        cheap_latency_base_ms=38.0,
        severity_matrix={
            "search_docs": 1.0,
            "ask_user": 2.0,
            "finish": 2.0,
            "database_lookup": 10.0,  # Unauthorized DB access is high severity
            "default": 2.0,
        },
    ),
    "incident_triage": WorkloadConfig(
        name="incident_triage",
        description="Production SRE telemetry alert triage and escalation",
        choices=("auto_mitigate", "page_oncall", "file_ticket", "suppress"),
        decision_site_share=0.25,  # 1 out of 4 LLM calls in monitoring pipeline
        total_decisions=5000,
        history_count=3500,
        eval_count=1500,
        teacher_in_cost_per_m=2.50,
        teacher_out_cost_per_m=10.00,
        teacher_latency_base_ms=130.0,
        cheap_in_cost_per_m=0.15,
        cheap_out_cost_per_m=0.60,
        cheap_latency_base_ms=30.0,
        severity_matrix={
            "file_ticket": 1.0,
            "auto_mitigate": 5.0,
            "page_oncall": 3.0,
            "suppress": 10.0,  # Missed outage / suppressing critical alert is 10.0
            "default": 2.0,
        },
    ),
    "research_novelty": WorkloadConfig(
        name="research_novelty",
        description="Negative Control: High-entropy open-ended web research with zero repetition",
        choices=("web_search", "synthesize", "extract_citations", "deep_read"),
        decision_site_share=0.15,  # 1 out of 7 LLM calls in open research agent
        total_decisions=3000,
        history_count=2100,
        eval_count=900,
        teacher_in_cost_per_m=3.00,
        teacher_out_cost_per_m=12.00,
        teacher_latency_base_ms=160.0,
        cheap_in_cost_per_m=0.20,
        cheap_out_cost_per_m=0.80,
        cheap_latency_base_ms=45.0,
        severity_matrix={
            "web_search": 1.0,
            "synthesize": 2.0,
            "extract_citations": 2.0,
            "deep_read": 3.0,
            "default": 1.0,
        },
        is_negative_control=True,
    ),
}


def _calculate_call_cost(in_tokens: int, out_tokens: int, in_per_m: float, out_per_m: float) -> float:
    return (in_tokens / 1_000_000.0) * in_per_m + (out_tokens / 1_000_000.0) * out_per_m


def generate_support_dataset(seed: int = 42) -> list[dict[str, Any]]:
    """Generates 5,000 decisions for the support ticket triage workload."""
    cfg = WORKLOAD_CONFIGS["support"]
    rng = random.Random(seed)

    def policy(state: dict[str, Any], drifted: bool) -> str:
        text = state.get("text", "").lower()
        amount = state.get("amount", 0)
        tier = state.get("tier", "standard")
        if tier == "enterprise" or amount >= 200 or "specialist" in text or "legal" in text:
            return "specialist"
        if "shattered" in text or "broken" in text or "damaged" in text or "cracked" in text:
            return "specialist" if drifted else "refund"
        if "tracking" in text or "where is" in text or "status" in text or "delivery date" in text:
            return "request_info"
        if "how to" in text or "manual" in text or "guide" in text:
            return "request_info"
        return "specialist" if amount > 60 else "request_info"

    templates = [
        ("Item shattered in transit, requesting full refund.", 35, "standard"),
        ("Package arrived broken and unusable.", 25, "standard"),
        ("Screen is cracked on delivery, need money back.", 45, "standard"),
        ("Tracking link not updating. Where is my package?", 0, "standard"),
        ("Order status shows delivered but I have not received it.", 0, "standard"),
        ("Estimated delivery date request for shipment.", 0, "standard"),
        ("Need user manual and setup guide for the device.", 0, "standard"),
        ("Enterprise licensing dispute and SLA credit request.", 600, "enterprise"),
        ("Priority escalated account audit and contract review.", 450, "enterprise"),
        ("Requesting specialist assistance for high value return.", 250, "standard"),
    ]

    prefixes = ["[Help] ", "[Urgent] ", "[Customer Support] ", "Hi, ", "Hello team, ", "Attention: ", ""]
    suffixes = [". Please reply.", ". Thanks.", ". Thank you.", ". Awaiting update.", ""]

    decisions = []
    total = cfg.total_decisions
    history_cutoff = cfg.history_count

    for i in range(total):
        is_eval = i >= history_cutoff
        eval_idx = i - history_cutoff if is_eval else i

        if not is_eval:
            # History period: first 70% (0..3499)
            phase = "history"
            drifted = False
            base_text, base_amt, base_tier = templates[i % len(templates)]
            text = base_text
        else:
            # Eval period: last 30% (3500..4999 -> 1500 items)
            if eval_idx < 300:
                phase = "eval_stable"
                drifted = False
                base_text, base_amt, base_tier = templates[i % len(templates)]
                text = base_text
            elif eval_idx < 600:
                phase = "eval_expansion"
                drifted = False
                base_text, base_amt, base_tier = templates[i % len(templates)]
                text = f"{rng.choice(prefixes)}{base_text}{rng.choice(suffixes)}"
            elif eval_idx < 900:
                phase = "eval_drift"
                drifted = True  # Injected policy shift!
                base_text, base_amt, base_tier = templates[i % len(templates)]
                text = f"{rng.choice(prefixes)}{base_text}{rng.choice(suffixes)}"
            elif eval_idx < 1200:
                phase = "eval_post_drift"
                drifted = True
                base_text, base_amt, base_tier = templates[i % len(templates)]
                text = base_text
            else:
                phase = "eval_recovery"
                drifted = True
                base_text, base_amt, base_tier = templates[i % len(templates)]
                text = f"[Case #{eval_idx}] {base_text}"

        state = {"text": text, "amount": base_amt, "tier": base_tier}
        true_choice = policy(state, drifted=drifted)

        # Teacher model behavior: knows current policy prompt (98.5% accurate on true choice)
        teacher_err = rng.random() < 0.015
        teacher_choice = true_choice if not teacher_err else rng.choice([c for c in cfg.choices if c != true_choice])

        # Cheap model behavior: 88% accurate on true choice, degraded confidence on boundary cases
        cheap_err = rng.random() < 0.12
        cheap_choice = true_choice if not cheap_err else rng.choice([c for c in cfg.choices if c != true_choice])
        cheap_conf = round(rng.uniform(0.72, 0.99) if not cheap_err else rng.uniform(0.40, 0.70), 3)

        in_toks = 140 + len(text.split())
        out_toks = 3
        teacher_cost = _calculate_call_cost(in_toks, out_toks, cfg.teacher_in_cost_per_m, cfg.teacher_out_cost_per_m)
        cheap_cost = _calculate_call_cost(in_toks, out_toks, cfg.cheap_in_cost_per_m, cfg.cheap_out_cost_per_m)
        teacher_lat = cfg.teacher_latency_base_ms + rng.uniform(-10.0, 25.0)
        cheap_lat = cfg.cheap_latency_base_ms + rng.uniform(-5.0, 10.0)

        decisions.append({
            "decision_id": f"supp_{i:05d}",
            "workload": "support",
            "index": i,
            "split": "eval" if is_eval else "history",
            "phase": phase,
            "state": state,
            "true_choice": true_choice,
            "teacher_choice": teacher_choice,
            "cheap_choice": cheap_choice,
            "cheap_confidence": cheap_conf,
            "input_tokens": in_toks,
            "output_tokens": out_toks,
            "teacher_cost_usd": teacher_cost,
            "cheap_cost_usd": cheap_cost,
            "teacher_latency_ms": teacher_lat,
            "cheap_latency_ms": cheap_lat,
            "drifted": drifted,
            "workflow_calls_total": 5,
        })

    return decisions


def generate_tool_select_dataset(seed: int = 142) -> list[dict[str, Any]]:
    """Generates 5,000 decisions for autonomous agent tool selection."""
    cfg = WORKLOAD_CONFIGS["tool_select"]
    rng = random.Random(seed)

    def policy(state: dict[str, Any], drifted: bool) -> str:
        q = state.get("query", "").lower()
        step = state.get("step", 1)
        if step >= 15 or "all tasks complete" in q or "final answer ready" in q:
            return "finish"
        if "database" in q or "sql" in q or "transaction ledger" in q or "account balance" in q:
            # Policy drift: Database ledger queries now require explicit user confirmation
            return "ask_user" if drifted else "database_lookup"
        if "clarify" in q or "permission" in q or "confirm irreversible" in q:
            return "ask_user"
        if "docs" in q or "documentation" in q or "api reference" in q or "changelog" in q or "search" in q:
            return "search_docs"
        return "search_docs" if step < 5 else "ask_user"

    templates = [
        ("Query database for recent transaction ledger and account balance.", 2),
        ("Lookup user ledger record in relational database.", 3),
        ("Execute database query to check payment transaction status.", 4),
        ("Search docs for Postgres 17 replication configuration.", 1),
        ("Look up API reference documentation for payment gateway endpoints.", 2),
        ("Search documentation changelog for breaking changes in version 2.", 1),
        ("Clarify parameters and confirm irreversible user deletion.", 5),
        ("Ask user for permission to execute destructive shell script.", 3),
        ("All tasks complete, generate final answer and terminate.", 16),
        ("Subtasks resolved, execution ready for final summary.", 18),
    ]

    prefixes = ["[Agent Step] ", "Task: ", "Action item: ", "Execute: ", "Step: ", ""]
    suffixes = [". Verify before execution.", ". Proceed.", ". Run now.", ""]

    decisions = []
    total = cfg.total_decisions
    history_cutoff = cfg.history_count

    for i in range(total):
        is_eval = i >= history_cutoff
        eval_idx = i - history_cutoff if is_eval else i

        if not is_eval:
            phase = "history"
            drifted = False
            base_query, base_step = templates[i % len(templates)]
            q = base_query
        else:
            if eval_idx < 300:
                phase = "eval_stable"
                drifted = False
                base_query, base_step = templates[i % len(templates)]
                q = base_query
            elif eval_idx < 600:
                phase = "eval_expansion"
                drifted = False
                base_query, base_step = templates[i % len(templates)]
                q = f"{rng.choice(prefixes)}{base_query}{rng.choice(suffixes)}"
            elif eval_idx < 900:
                phase = "eval_drift"
                drifted = True  # Injected policy shift!
                base_query, base_step = templates[i % len(templates)]
                q = f"{rng.choice(prefixes)}{base_query}{rng.choice(suffixes)}"
            elif eval_idx < 1200:
                phase = "eval_post_drift"
                drifted = True
                base_query, base_step = templates[i % len(templates)]
                q = base_query
            else:
                phase = "eval_recovery"
                drifted = True
                base_query, base_step = templates[i % len(templates)]
                q = f"[Iteration #{eval_idx}] {base_query}"

        state = {"query": q, "step": base_step}
        true_choice = policy(state, drifted=drifted)

        teacher_err = rng.random() < 0.012
        teacher_choice = true_choice if not teacher_err else rng.choice([c for c in cfg.choices if c != true_choice])

        cheap_err = rng.random() < 0.14
        cheap_choice = true_choice if not cheap_err else rng.choice([c for c in cfg.choices if c != true_choice])
        cheap_conf = round(rng.uniform(0.70, 0.98) if not cheap_err else rng.uniform(0.38, 0.68), 3)

        in_toks = 180 + len(q.split())
        out_toks = 4
        teacher_cost = _calculate_call_cost(in_toks, out_toks, cfg.teacher_in_cost_per_m, cfg.teacher_out_cost_per_m)
        cheap_cost = _calculate_call_cost(in_toks, out_toks, cfg.cheap_in_cost_per_m, cfg.cheap_out_cost_per_m)
        teacher_lat = cfg.teacher_latency_base_ms + rng.uniform(-12.0, 30.0)
        cheap_lat = cfg.cheap_latency_base_ms + rng.uniform(-6.0, 12.0)

        decisions.append({
            "decision_id": f"tool_{i:05d}",
            "workload": "tool_select",
            "index": i,
            "split": "eval" if is_eval else "history",
            "phase": phase,
            "state": state,
            "true_choice": true_choice,
            "teacher_choice": teacher_choice,
            "cheap_choice": cheap_choice,
            "cheap_confidence": cheap_conf,
            "input_tokens": in_toks,
            "output_tokens": out_toks,
            "teacher_cost_usd": teacher_cost,
            "cheap_cost_usd": cheap_cost,
            "teacher_latency_ms": teacher_lat,
            "cheap_latency_ms": cheap_lat,
            "drifted": drifted,
            "workflow_calls_total": 8,
        })

    return decisions


def generate_incident_triage_dataset(seed: int = 242) -> list[dict[str, Any]]:
    """Generates 5,000 decisions for SRE telemetry alert escalation triage."""
    cfg = WORKLOAD_CONFIGS["incident_triage"]
    rng = random.Random(seed)

    def policy(state: dict[str, Any], drifted: bool) -> str:
        msg = state.get("alert_message", "").lower()
        sev = state.get("severity", "P3")
        cluster = state.get("cluster", "prod-us-east")
        if sev == "P0" or "database outage" in msg or "payment pipeline failure" in msg:
            return "page_oncall"
        if "intermittent memory spike" in msg or "container memory alert" in msg:
            # Policy drift: Intermittent memory alerts now auto-mitigate (pod restart) instead of paging on-call
            return "auto_mitigate" if drifted else "page_oncall"
        if "disk usage above 85" in msg or "disk pressure" in msg:
            return "auto_mitigate"
        if sev == "P1":
            return "page_oncall"
        if sev == "P2" or "tls certificate expiring in 14 days" in msg or "slow query warning" in msg:
            return "file_ticket"
        if "flapping probe" in msg or "heartbeat jitter" in msg or sev == "P4":
            return "suppress"
        return "file_ticket"

    templates = [
        ("CRITICAL: Database outage in primary payment shard.", "P0", "prod-us-east"),
        ("CRITICAL: Payment pipeline failure affecting checkout.", "P0", "prod-eu-west"),
        ("WARNING: Intermittent memory spike on worker node.", "P1", "prod-us-east"),
        ("ALERT: Container memory alert exceeding threshold.", "P1", "prod-us-east"),
        ("NOTICE: Disk usage above 85 percent on log volume.", "P2", "prod-us-central"),
        ("WARN: Disk pressure detected on cache pod.", "P2", "prod-us-west"),
        ("NOTICE: TLS certificate expiring in 14 days for internal api.", "P3", "prod-us-east"),
        ("INFO: Slow query warning on non-indexed report view.", "P3", "prod-ap-south"),
        ("DEBUG: Flapping probe detected on staging ingress.", "P4", "staging"),
        ("INFO: Heartbeat jitter below drop threshold.", "P4", "prod-us-east"),
    ]

    prefixes = ["[Telemetry] ", "[Datadog Alert] ", "[PagerDuty Event] ", "ALERT: ", ""]
    suffixes = [". Action required.", ". Immediate check.", ". Automated telemetry.", ""]

    decisions = []
    total = cfg.total_decisions
    history_cutoff = cfg.history_count

    for i in range(total):
        is_eval = i >= history_cutoff
        eval_idx = i - history_cutoff if is_eval else i

        if not is_eval:
            phase = "history"
            drifted = False
            base_msg, base_sev, base_cluster = templates[i % len(templates)]
            msg = base_msg
        else:
            if eval_idx < 300:
                phase = "eval_stable"
                drifted = False
                base_msg, base_sev, base_cluster = templates[i % len(templates)]
                msg = base_msg
            elif eval_idx < 600:
                phase = "eval_expansion"
                drifted = False
                base_msg, base_sev, base_cluster = templates[i % len(templates)]
                msg = f"{rng.choice(prefixes)}{base_msg}{rng.choice(suffixes)}"
            elif eval_idx < 900:
                phase = "eval_drift"
                drifted = True  # Injected policy shift!
                base_msg, base_sev, base_cluster = templates[i % len(templates)]
                msg = f"{rng.choice(prefixes)}{base_msg}{rng.choice(suffixes)}"
            elif eval_idx < 1200:
                phase = "eval_post_drift"
                drifted = True
                base_msg, base_sev, base_cluster = templates[i % len(templates)]
                msg = base_msg
            else:
                phase = "eval_recovery"
                drifted = True
                base_msg, base_sev, base_cluster = templates[i % len(templates)]
                msg = f"[Alert #{eval_idx}] {base_msg}"

        state = {"alert_message": msg, "severity": base_sev, "cluster": base_cluster}
        true_choice = policy(state, drifted=drifted)

        teacher_err = rng.random() < 0.010
        teacher_choice = true_choice if not teacher_err else rng.choice([c for c in cfg.choices if c != true_choice])

        cheap_err = rng.random() < 0.11
        cheap_choice = true_choice if not cheap_err else rng.choice([c for c in cfg.choices if c != true_choice])
        cheap_conf = round(rng.uniform(0.74, 0.99) if not cheap_err else rng.uniform(0.42, 0.70), 3)

        in_toks = 160 + len(msg.split())
        out_toks = 3
        teacher_cost = _calculate_call_cost(in_toks, out_toks, cfg.teacher_in_cost_per_m, cfg.teacher_out_cost_per_m)
        cheap_cost = _calculate_call_cost(in_toks, out_toks, cfg.cheap_in_cost_per_m, cfg.cheap_out_cost_per_m)
        teacher_lat = cfg.teacher_latency_base_ms + rng.uniform(-10.0, 25.0)
        cheap_lat = cfg.cheap_latency_base_ms + rng.uniform(-5.0, 10.0)

        decisions.append({
            "decision_id": f"inc_{i:05d}",
            "workload": "incident_triage",
            "index": i,
            "split": "eval" if is_eval else "history",
            "phase": phase,
            "state": state,
            "true_choice": true_choice,
            "teacher_choice": teacher_choice,
            "cheap_choice": cheap_choice,
            "cheap_confidence": cheap_conf,
            "input_tokens": in_toks,
            "output_tokens": out_toks,
            "teacher_cost_usd": teacher_cost,
            "cheap_cost_usd": cheap_cost,
            "teacher_latency_ms": teacher_lat,
            "cheap_latency_ms": cheap_lat,
            "drifted": drifted,
            "workflow_calls_total": 4,
        })

    return decisions


def generate_research_novelty_dataset(seed: int = 342) -> list[dict[str, Any]]:
    """Generates 3,000 decisions for high-entropy open web research (Negative Control)."""
    cfg = WORKLOAD_CONFIGS["research_novelty"]
    rng = random.Random(seed)

    topics = [
        "quantum entanglement decoherence rates", "macroeconomic inflation forecasting models",
        "crispr gene therapy delivery vectors", "byzantine fault tolerance algorithms",
        "ancient mesopotamian irrigation hydrology", "graphene semiconductor bandgap engineering",
        "deep sea hydrothermal vent extremophiles", "synthetic biology metabolic flux analysis",
        "neuroplasticity synaptic pruning pathways", "hypersonic aerodynamics boundary layer transition",
        "algorithmic mechanism design auctions", "paleoclimatology ice core isotopic ratios",
    ]
    intents = ["literature review", "empirical benchmark analysis", "theoretical proof verification", "historical timeline synthesis"]

    def policy(state: dict[str, Any], drifted: bool) -> str:
        q = state.get("query", "").lower()
        if "benchmark" in q or "empirical" in q or "data" in q:
            return "extract_citations"
        if "review" in q or "literature" in q:
            return "web_search"
        if "proof" in q or "theory" in q:
            return "deep_read"
        return "synthesize"

    decisions = []
    total = cfg.total_decisions
    history_cutoff = cfg.history_count

    for i in range(total):
        is_eval = i >= history_cutoff
        # Extreme novelty: unique UUID, randomized topics, unique session tokens
        topic = rng.choice(topics)
        intent = rng.choice(intents)
        unique_token = f"{rng.randint(100000, 999999)}-{i}"
        query = f"Investigate {topic} focusing on {intent} [session_{unique_token}]."

        state = {"query": query, "session_id": unique_token, "intent": intent}
        true_choice = policy(state, drifted=False)

        teacher_err = rng.random() < 0.02
        teacher_choice = true_choice if not teacher_err else rng.choice([c for c in cfg.choices if c != true_choice])

        cheap_err = rng.random() < 0.16
        cheap_choice = true_choice if not cheap_err else rng.choice([c for c in cfg.choices if c != true_choice])
        cheap_conf = round(rng.uniform(0.65, 0.95) if not cheap_err else rng.uniform(0.35, 0.65), 3)

        in_toks = 220 + len(query.split())
        out_toks = 5
        teacher_cost = _calculate_call_cost(in_toks, out_toks, cfg.teacher_in_cost_per_m, cfg.teacher_out_cost_per_m)
        cheap_cost = _calculate_call_cost(in_toks, out_toks, cfg.cheap_in_cost_per_m, cfg.cheap_out_cost_per_m)
        teacher_lat = cfg.teacher_latency_base_ms + rng.uniform(-15.0, 35.0)
        cheap_lat = cfg.cheap_latency_base_ms + rng.uniform(-8.0, 15.0)

        decisions.append({
            "decision_id": f"res_{i:05d}",
            "workload": "research_novelty",
            "index": i,
            "split": "eval" if is_eval else "history",
            "phase": "eval_novelty" if is_eval else "history",
            "state": state,
            "true_choice": true_choice,
            "teacher_choice": teacher_choice,
            "cheap_choice": cheap_choice,
            "cheap_confidence": cheap_conf,
            "input_tokens": in_toks,
            "output_tokens": out_toks,
            "teacher_cost_usd": teacher_cost,
            "cheap_cost_usd": cheap_cost,
            "teacher_latency_ms": teacher_lat,
            "cheap_latency_ms": cheap_lat,
            "drifted": False,
            "workflow_calls_total": 7,
        })

    return decisions


def get_workload_dataset(name: str, seed: int = 42) -> tuple[WorkloadConfig, list[dict[str, Any]]]:
    """Retrieves dataset and configuration for the given workload name."""
    if name not in WORKLOAD_CONFIGS:
        raise ValueError(f"Unknown workload: {name}. Available: {list(WORKLOAD_CONFIGS.keys())}")
    cfg = WORKLOAD_CONFIGS[name]
    if name == "support":
        items = generate_support_dataset(seed)
    elif name == "tool_select":
        items = generate_tool_select_dataset(seed)
    elif name == "incident_triage":
        items = generate_incident_triage_dataset(seed)
    elif name == "research_novelty":
        items = generate_research_novelty_dataset(seed)
    else:
        raise ValueError(name)
    return cfg, items
