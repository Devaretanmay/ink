"""Conformance test suite for Generalized Behavior JIT architecture.

Tests:
- Workload A: Structured/categorical states (ExactEngine fast-path)
- Workload B: Natural language routing with semantic variation
- Workload C: Cross-domain technical workload (SRE severity triage)
- Workload D: Novel / Out-of-Distribution (OOD) rejection
- Semantic region acceptance / rejection bounds
- Drift detection and automatic deoptimization
"""

import math
import time
from pathlib import Path
import tempfile
import pytest

from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements
from ink.internal.representation import StateRepresentationLayer, RepresentedState
from ink.internal.coverage import CoverageEngine, CoverageRegion, TextVectorizer
from ink.internal.engines import ExactEngine, LinearClassifierEngine, get_engine_metadata
from ink.internal.selection import EngineSelectionPolicy, evaluate_engine_candidate


def test_state_representation_layer_partitions_fields():
    """Verify StateRepresentationLayer separates exact, structured, and semantic fields."""
    schema = {
        "customer_tier": "string",
        "retry_count": "integer",
        "amount": "number",
        "ticket_text": "string",
        "flagged": "boolean",
    }
    rep = StateRepresentationLayer(schema)
    raw_state = {
        "customer_tier": "gold",
        "retry_count": 2,
        "amount": 149.99,
        "ticket_text": "I was double billed for my subscription last Tuesday",
        "flagged": False,
    }
    represented = rep.represent(raw_state)

    assert represented.exact_features["customer_tier"] == "gold"
    assert represented.exact_features["retry_count"] == 2
    assert represented.exact_features["flagged"] is False
    assert represented.structured_features["amount"] == 149.99
    assert "ticket_text" in represented.semantic_features
    assert "double billed" in represented.semantic_text
    assert len(represented.semantic_vector) > 0


def test_conformance_workload_a_structured_state(tmp_path):
    """Workload A: Structured/categorical states preserve ExactEngine 100% precision."""
    db_path = str(tmp_path / "structured.db")
    site = DecisionSite(
        "payment.dispute_action",
        {"tier": "string", "amount_bracket": "string", "prior_disputes": "integer"},
        ("auto_refund", "manual_investigate", "deny"),
    )
    reqs = PromotionRequirements(
        min_samples=6,
        min_quality=0.5,
        min_confidence=0.2,
        max_degradation=1.0,
        comparison_rate=0.05,
        min_region_samples=2,
        evaluation_window=20,
    )

    def verifier(state, choice):
        if state["tier"] == "enterprise" and state["amount_bracket"] == "low":
            expected = "auto_refund"
        elif state["prior_disputes"] > 3:
            expected = "deny"
        else:
            expected = "manual_investigate"
        return Outcome(1.0 if choice == expected else 0.0, "dispute_ledger", "1", {"state": state})

    with Ink(db_path, maintenance_verifier=verifier, maintenance_requirements=reqs) as ink:
        ink.register(site)

        structured_samples = [
            ({"tier": "enterprise", "amount_bracket": "low", "prior_disputes": 0}, "auto_refund"),
            ({"tier": "consumer", "amount_bracket": "high", "prior_disputes": 0}, "manual_investigate"),
            ({"tier": "consumer", "amount_bracket": "low", "prior_disputes": 5}, "deny"),
        ]

        # 90 observations (30 per state -> 6 per state in holdout >= min_samples)
        for i in range(90):
            sample_state, sample_choice = structured_samples[i % len(structured_samples)]
            res = ink.decide(
                site=site,
                state=sample_state,
                fallback=lambda sc=sample_choice: FallbackResult(sc, cost=0.002, model_calls=1),
                task_id=f"struct_task_{i}",
            )
            outcome = verifier(sample_state, res.choice)
            ink.record_outcome(res.decision_id, quality=outcome.quality, verifier=outcome.verifier, verifier_version=outcome.verifier_version)

        art_id = ink.compile(site, engine="exact")
        assert art_id is not None

        profile = ink.calibrate(site, verifier=verifier, requirements=reqs)
        assert profile is not None
        assert len(profile["coverage"]) > 0

        # Feed 24 shadow observations (8 per state >= min_samples)
        for i in range(24):
            sample_state, sample_choice = structured_samples[i % len(structured_samples)]
            res = ink.decide(
                site=site,
                state=sample_state,
                fallback=lambda sc=sample_choice: FallbackResult(sc, cost=0.002, model_calls=1),
                task_id=f"shadow_struct_{i}",
            )
            outcome = verifier(sample_state, res.choice)
            ink.record_outcome(res.decision_id, quality=outcome.quality, verifier=outcome.verifier, verifier_version=outcome.verifier_version)

        eval_res = ink.evaluate(site, verifier=verifier, auto_promote=True)
        status = ink.inspect(site)
        assert status["state"] == "ACTIVE"
        assert status["selected_engine"] == "exact"

        # Test local exact fast path serving
        test_state = {"tier": "enterprise", "amount_bracket": "low", "prior_disputes": 0}
        fast_res = ink.decide(
            site=site,
            state=test_state,
            fallback=lambda: FallbackResult("deny", cost=0.002, model_calls=1),
        )
        assert fast_res.source == "fast_path"
        assert fast_res.choice == "auto_refund"


def test_conformance_workload_b_natural_language_routing(tmp_path):
    """Workload B: Unseen natural language variations qualify and serve locally."""
    db_path = str(tmp_path / "nl_routing.db")
    site = DecisionSite(
        "support.intent_routing",
        {"user_message": "string"},
        ("card_replacement", "dispute_charge", "account_security"),
    )
    reqs = PromotionRequirements(
        min_samples=6,
        min_quality=0.5,
        min_confidence=0.2,
        max_degradation=1.0,
        comparison_rate=0.05,
        min_region_samples=2,
        evaluation_window=20,
    )

    def verifier(state, choice):
        msg = state["user_message"].lower()
        if any(w in msg for w in ("card", "replacement", "stolen", "debit")):
            expected = "card_replacement"
        elif any(w in msg for w in ("billed", "charge", "refund", "receipt")):
            expected = "dispute_charge"
        else:
            expected = "account_security"
        return Outcome(1.0 if choice == expected else 0.0, "intent_oracle", "1", {"msg": msg})

    nl_corpus = [
        # card_replacement variations
        ("My card hasn't arrived yet", "card_replacement"),
        ("Still waiting for my new debit card", "card_replacement"),
        ("Where is the replacement card you mailed?", "card_replacement"),
        ("Lost my physical card yesterday", "card_replacement"),
        # dispute_charge variations
        ("I was charged twice for lunch today", "dispute_charge"),
        ("Please refund duplicate charge on statement", "dispute_charge"),
        ("Unrecognized charge on my receipt", "dispute_charge"),
        ("Why was I billed two times for subscription", "dispute_charge"),
        # account_security variations
        ("Suspicious login attempt detected on profile", "account_security"),
        ("Need to update password and security questions", "account_security"),
        ("Two factor auth code not working", "account_security"),
        ("Someone unauthorized accessed my profile", "account_security"),
    ]

    with Ink(db_path, maintenance_verifier=verifier, maintenance_requirements=reqs) as ink:
        ink.register(site)

        # 120 observations (cycling through corpus with slight phrasing differences)
        for i in range(120):
            base_text, choice = nl_corpus[i % len(nl_corpus)]
            text = f"{base_text} ref-{i}"
            state = {"user_message": text}
            res = ink.decide(
                site=site,
                state=state,
                fallback=lambda c=choice: FallbackResult(c, cost=0.003, model_calls=1),
                task_id=f"nl_task_{i}",
            )
            outcome = verifier(state, res.choice)
            ink.record_outcome(res.decision_id, quality=outcome.quality, verifier=outcome.verifier, verifier_version=outcome.verifier_version)

        art_id = ink.compile(site, engine="auto")
        assert art_id is not None

        profile = ink.calibrate(site, verifier=verifier, requirements=reqs)
        assert profile is not None

        # Shadow period
        for i in range(24):
            base_text, choice = nl_corpus[i % len(nl_corpus)]
            state = {"user_message": f"Shadow query: {base_text} #{i}"}
            res = ink.decide(
                site=site,
                state=state,
                fallback=lambda c=choice: FallbackResult(c, cost=0.003, model_calls=1),
                task_id=f"nl_shadow_{i}",
            )
            outcome = verifier(state, res.choice)
            ink.record_outcome(res.decision_id, quality=outcome.quality, verifier=outcome.verifier, verifier_version=outcome.verifier_version)

        ink.evaluate(site, verifier=verifier, auto_promote=True)
        inspect_data = ink.inspect(site)
        assert inspect_data["state"] == "ACTIVE"
        assert inspect_data["selected_engine"] in ("linear", "classifier", "decision")

        # Test local semantic fast path serving
        results = [
            ink.decide(
                site=site,
                state={"user_message": "Where is my replacement debit card?"},
                fallback=lambda: "dispute_charge",
            )
            for _ in range(5)
        ]
        assert any(r.source == "fast_path" for r in results)
        assert all(r.choice == "card_replacement" for r in results if r.source == "fast_path")


def test_conformance_workload_c_cross_domain_sre_severity(tmp_path):
    """Workload C: SRE alert severity triage (totally distinct non-support domain)."""
    db_path = str(tmp_path / "sre_triage.db")
    site = DecisionSite(
        "sre.incident_severity",
        {"service_name": "string", "incident_summary": "string", "error_code": "string"},
        ("p0_critical", "p1_major", "p2_minor", "p3_info"),
    )
    reqs = PromotionRequirements(
        min_samples=6,
        min_quality=0.5,
        min_confidence=0.2,
        max_degradation=1.0,
        comparison_rate=0.05,
        min_region_samples=2,
        evaluation_window=20,
    )

    def sre_verifier(state, choice):
        code = state["error_code"]
        summary = state["incident_summary"].lower()
        if "data_corruption" in code or "outage" in summary:
            expected = "p0_critical"
        elif "latency_spike" in code or "degraded" in summary:
            expected = "p1_major"
        elif "warning" in code or "high_cpu" in summary:
            expected = "p2_minor"
        else:
            expected = "p3_info"
        return Outcome(1.0 if choice == expected else 0.0, "pagerduty_oracle", "1", {"state": state})

    incidents = [
        ({"service_name": "auth-db", "incident_summary": "Complete database outage and corruption", "error_code": "data_corruption_err"}, "p0_critical"),
        ({"service_name": "payments-api", "incident_summary": "Degraded p99 latency spike to 4000ms", "error_code": "latency_spike_504"}, "p1_major"),
        ({"service_name": "search-worker", "incident_summary": "High CPU utilization on worker node 4", "error_code": "warning_threshold_70"}, "p2_minor"),
        ({"service_name": "metrics-agent", "incident_summary": "Routine certificate renewal notification", "error_code": "info_notice_cert"}, "p3_info"),
    ]

    with Ink(db_path, maintenance_verifier=sre_verifier, maintenance_requirements=reqs) as ink:
        ink.register(site)

        # 120 observations across 4 classes (30 per class -> 6 per class in holdout >= min_samples)
        for i in range(120):
            base_state, choice = incidents[i % len(incidents)]
            state = dict(base_state)
            state["incident_summary"] = f"{base_state['incident_summary']} event-{i}"
            res = ink.decide(
                site=site,
                state=state,
                fallback=lambda c=choice: FallbackResult(c, cost=0.005, model_calls=1),
                task_id=f"sre_task_{i}",
            )
            out = sre_verifier(state, res.choice)
            ink.record_outcome(res.decision_id, quality=out.quality, verifier=out.verifier, verifier_version=out.verifier_version)

        art_id = ink.compile(site, engine="linear")
        assert art_id is not None

        ink.calibrate(site, verifier=sre_verifier, requirements=reqs)

        # 24 shadow observations (6 per class >= min_samples)
        for i in range(24):
            base_state, choice = incidents[i % len(incidents)]
            state = dict(base_state)
            state["incident_summary"] = f"Shadow trace {base_state['incident_summary']} {i}"
            res = ink.decide(
                site=site,
                state=state,
                fallback=lambda c=choice: FallbackResult(c, cost=0.005, model_calls=1),
                task_id=f"sre_shadow_{i}",
            )
            out = sre_verifier(state, res.choice)
            ink.record_outcome(res.decision_id, quality=out.quality, verifier=out.verifier, verifier_version=out.verifier_version)

        ink.evaluate(site, verifier=sre_verifier, auto_promote=True)
        inspect_data = ink.inspect(site)
        assert inspect_data["state"] == "ACTIVE"
        assert inspect_data["selected_engine"] == "linear"


def test_conformance_workload_d_novelty_and_ood_rejection(tmp_path):
    """Workload D: True novelty / OOD queries cleanly fallback to host model."""
    db_path = str(tmp_path / "ood_rejection.db")
    site = DecisionSite(
        "legal.compliance_routing",
        {"document_text": "string"},
        ("gdpr_privacy", "sox_financial", "export_control"),
    )
    reqs = PromotionRequirements(
        min_samples=6,
        min_quality=0.5,
        min_confidence=0.2,
        max_degradation=1.0,
        comparison_rate=0.05,
        min_region_samples=2,
        evaluation_window=20,
    )

    def legal_verifier(state, choice):
        t = state["document_text"].lower()
        if "gdpr" in t or "data subject" in t or "privacy" in t:
            expected = "gdpr_privacy"
        elif "sarbanes" in t or "audit" in t or "financial" in t:
            expected = "sox_financial"
        else:
            expected = "export_control"
        return Outcome(1.0 if choice == expected else 0.0, "legal_verifier", "1", {})

    corpus = [
        ("GDPR data subject deletion request for European user", "gdpr_privacy"),
        ("Privacy policy update under EU GDPR regulations", "gdpr_privacy"),
        ("Sarbanes Oxley internal financial audit trail", "sox_financial"),
        ("Financial statements quarterly compliance review", "sox_financial"),
        ("Export control classification number for encryption software", "export_control"),
        ("ITAR defense articles export authorization permit", "export_control"),
    ]

    with Ink(db_path, maintenance_verifier=legal_verifier, maintenance_requirements=reqs) as ink:
        ink.register(site)

        # 120 observations (20 per corpus item -> 8 per choice in holdout >= min_samples)
        for i in range(120):
            text, choice = corpus[i % len(corpus)]
            state = {"document_text": f"{text} #{i}"}
            res = ink.decide(
                site=site,
                state=state,
                fallback=lambda c=choice: FallbackResult(c, cost=0.004, model_calls=1),
                task_id=f"legal_task_{i}",
            )
            out = legal_verifier(state, res.choice)
            ink.record_outcome(res.decision_id, quality=out.quality, verifier=out.verifier, verifier_version=out.verifier_version)

        ink.compile(site, engine="linear")
        ink.calibrate(site, verifier=legal_verifier, requirements=reqs)

        # 24 shadow observations (8 per choice >= min_samples)
        for i in range(24):
            text, choice = corpus[i % len(corpus)]
            state = {"document_text": f"Shadow review {text} #{i}"}
            res = ink.decide(
                site=site,
                state=state,
                fallback=lambda c=choice: FallbackResult(c, cost=0.004, model_calls=1),
                task_id=f"legal_shadow_{i}",
            )
            out = legal_verifier(state, res.choice)
            ink.record_outcome(res.decision_id, quality=out.quality, verifier=out.verifier, verifier_version=out.verifier_version)

        ink.evaluate(site, verifier=legal_verifier, auto_promote=True)
        assert ink.inspect(site)["state"] == "ACTIVE"

        # 1. Known semantic variation -> Served locally
        results = [
            ink.decide(
                site=site,
                state={"document_text": "GDPR privacy policy deletion request for user profile"},
                fallback=lambda: FallbackResult("export_control", cost=0.004, model_calls=1),
            )
            for _ in range(5)
        ]
        assert any(r.source == "fast_path" for r in results)
        assert all(r.choice == "gdpr_privacy" for r in results if r.source == "fast_path")

        # 2. Alien / OOD query -> Must reject and fallback to host model!
        alien_state = {"document_text": "Order a pepperoni pizza with extra mozzarella cheese and garlic bread"}
        alien_res = ink.decide(
            site=site,
            state=alien_state,
            fallback=lambda: FallbackResult("export_control", cost=0.004, model_calls=1),
        )
        assert alien_res.source == "fallback"
        assert alien_res.fallback_reason == "outside_coverage"


def test_drift_and_deoptimization(tmp_path):
    """Drift test: Degraded comparison outcomes automatically deoptimize the Fast Path."""
    db_path = str(tmp_path / "drift_test.db")
    site = DecisionSite(
        "security.access_gate",
        {"role": "string", "ip_country": "string"},
        ("grant_access", "require_mfa", "block"),
    )
    reqs = PromotionRequirements(
        min_samples=6,
        min_quality=0.5,
        min_confidence=0.2,
        max_degradation=1.0,
        comparison_rate=0.5,
        min_region_samples=2,
        evaluation_window=20,
    )

    policy_drift = False

    def access_verifier(state, choice):
        if not policy_drift:
            expected = "grant_access" if state["role"] == "admin" else "require_mfa"
        else:
            expected = "block" if state["ip_country"] != "US" else "grant_access"
        return Outcome(1.0 if choice == expected else 0.0, "iam_authority", "1", {"state": state})

    with Ink(db_path, maintenance_verifier=access_verifier, maintenance_requirements=reqs) as ink:
        ink.register(site)

        states = [
            ({"role": "admin", "ip_country": "DE"}, "grant_access"),
            ({"role": "user", "ip_country": "US"}, "require_mfa"),
        ]
        for i in range(60):
            st, ch = states[i % len(states)]
            res = ink.decide(
                site=site,
                state=st,
                fallback=lambda c=ch: FallbackResult(c, cost=0.002, model_calls=1),
                task_id=f"iam_task_{i}",
            )
            out = access_verifier(st, res.choice)
            ink.record_outcome(res.decision_id, quality=out.quality, verifier=out.verifier, verifier_version=out.verifier_version)

        ink.compile(site, engine="exact")
        ink.calibrate(site, verifier=access_verifier, requirements=reqs)

        for i in range(24):
            st, ch = states[i % len(states)]
            res = ink.decide(
                site=site,
                state=st,
                fallback=lambda c=ch: FallbackResult(c, cost=0.002, model_calls=1),
                task_id=f"iam_shadow_{i}",
            )
            out = access_verifier(st, res.choice)
            ink.record_outcome(res.decision_id, quality=out.quality, verifier=out.verifier, verifier_version=out.verifier_version)

        ink.evaluate(site, verifier=access_verifier, auto_promote=True)
        assert ink.inspect(site)["state"] == "ACTIVE"

        policy_drift = True

        drifted_st = {"role": "admin", "ip_country": "DE"}
        incorrect_serves = 0
        comparison_serves = 0

        for i in range(20):
            res = ink.decide(
                site=site,
                state=drifted_st,
                fallback=lambda: FallbackResult("block", cost=0.002, model_calls=1),
                task_id=f"drift_task_{i}",
            )
            out = access_verifier(drifted_st, res.choice)
            ink.record_outcome(res.decision_id, quality=out.quality, verifier=out.verifier, verifier_version=out.verifier_version)

            if res.source == "fast_path" and res.choice != "block":
                incorrect_serves += 1
            elif res.source == "fallback" and res.fallback_reason == "comparison":
                comparison_serves += 1

            ink.maintenance()

        status = ink.inspect(site)
        assert status["state"] in ("DEMOTED", "SHADOW", "ACTIVE")
        assert incorrect_serves >= 1
        assert comparison_serves >= 1
