"""Operational demo acceptance must fail visibly when qualification is incomplete."""

from examples.refund_agent.agent import Ledger, lifecycle_acceptance, rules


def test_incomplete_report_cannot_pass_release_gate():
    assert not all(lifecycle_acceptance({"phases": {}}).values())


def test_ledger_checks_actual_actions_and_drift():
    state = {
        "amount": 42.5,
        "payment_status": "settled",
        "chargeback": False,
        "merchant_consistent": True,
        "customer_tier": "pro",
    }
    ledger = Ledger()
    try:
        choice = rules(state).choice
        assert ledger.execute(state, choice).quality == 1
        ledger.limit = 10
        assert ledger.execute(state, choice).quality == 0
        assert ledger.execute(state, "specialist").quality == 1
        assert ledger.db.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 3
    finally:
        ledger.close()


def test_remote_retry_usage_separates_attempts(monkeypatch):
    import io
    import json
    import urllib.error

    from examples.refund_agent.agent import model_fallback

    monkeypatch.setenv("INK_API_KEY", "test-only")
    attempts = []

    def transport(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "busy", {"Retry-After": "0"}, None)
        return io.BytesIO(
            json.dumps(
                {
                    "choices": [{"message": {"content": '{"choice":"refund"}'}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 5},
                }
            ).encode()
        )

    monkeypatch.setattr("urllib.request.urlopen", transport)
    result = model_fallback({"amount": 42.5}, "test-provider")
    assert result.choice == "refund"
    assert result.request_attempts == 2 and result.model_calls == 1
    assert result.input_tokens == 20 and result.output_tokens == 5
