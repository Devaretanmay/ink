"""Support-triage routing decision site, wrapped with Ink."""

import time
from typing import Any

from ink import DecisionSite, Ink


class IntegratedSupportRouter:
    def __init__(self, client: Ink, fallback_revision: str = "1"):
        self.ml = client
        self.site = DecisionSite(
            name="support.triage_route",
            state_schema={"ticket_text": "string", "customer_tier": "string"},
            choices=(
                "tier1_faq",
                "billing_refund",
                "tech_escalation",
                "account_security",
                "close_duplicate",
            ),
            fallback_revision=fallback_revision,
        )
        self.ml.register(self.site)
        self.model_calls = 0
        self.total_cost = 0.0
        self.latencies = []

    def _cloud_model_fallback(self, state: dict[str, Any], policy_v2: bool = False) -> str:
        self.model_calls += 1
        self.total_cost += 0.0028
        time.sleep(0.005)

        text = state.get("ticket_text", "").lower()
        if "credit card" in text or "tracking" in text:
            return "tier1_faq"
        if "refund" in text:
            if policy_v2 and state.get("customer_tier") == "free":
                return "account_security"
            return "billing_refund"
        if "crash" in text or "timeout" in text:
            return "tech_escalation"
        if "suspicious" in text or "unknown ip" in text:
            return "account_security"
        return "close_duplicate"

    def process_ticket(
        self,
        ticket: dict[str, Any],
        policy_v2: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        # Developer review: exclude volatile session_id and created_at_epoch
        clean_state = {
            "ticket_text": str(ticket.get("ticket_text", "")),
            "customer_tier": str(ticket.get("customer_tier", "standard")),
        }

        decision = self.ml.decide(
            site=self.site.name,
            state=clean_state,
            fallback=lambda: self._cloud_model_fallback(clean_state, policy_v2=policy_v2),
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.latencies.append(elapsed_ms)

        self.ml.record_outcome(
            decision.decision_id,
            quality=1.0,
            verifier="ticket_resolution_status",
            verifier_version="1",
            evidence={"resolved": True, "reopened": False},
        )

        return {
            "route": decision.choice,
            "decision_source": decision.source,
            "decision_id": decision.decision_id,
        }
