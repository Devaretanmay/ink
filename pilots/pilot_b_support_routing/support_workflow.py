"""Pilot B: Enterprise Support Triage & Workflow Router (Before Ink)."""

import time
from typing import Any


class UninstrumentedSupportRouter:
    def __init__(self):
        self.model_calls = 0
        self.total_cost = 0.0
        self.latencies = []

    def mock_llm_triage(self, ticket: dict[str, Any], policy_v2: bool = False) -> str:
        self.model_calls += 1
        self.total_cost += 0.0028
        time.sleep(0.005)
        self.latencies.append(300.0)

        text = ticket.get("ticket_text", "").lower()
        if "credit card" in text or "tracking" in text:
            return "tier1_faq"
        if "refund" in text:
            # Policy change in v2: high fraud risk refunds require account_security
            if policy_v2 and ticket.get("customer_tier") == "free":
                return "account_security"
            return "billing_refund"
        if "crash" in text or "timeout" in text:
            return "tech_escalation"
        if "suspicious" in text or "unknown ip" in text:
            return "account_security"
        return "close_duplicate"

    def process_ticket(self, ticket: dict[str, Any], policy_v2: bool = False) -> dict[str, Any]:
        route = self.mock_llm_triage(ticket, policy_v2=policy_v2)
        return {"route": route, "ticket_id": ticket.get("session_id")}
