"""A generated operational workload with independently executed ledger outcomes."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path

from microloop import DecisionSite, FallbackResult, Microloop, Outcome, PromotionRequirements
from microloop.internal.engines import LayaEngine

SITE = DecisionSite(
    "refund.next_action",
    {
        "amount": "number",
        "payment_status": "string",
        "chargeback": "boolean",
        "customer_tier": "string",
        "merchant_consistent": "boolean",
    },
    ("refund", "request_information", "specialist"),
)
CASES = [
    {
        "amount": 42.5,
        "payment_status": "settled",
        "chargeback": False,
        "customer_tier": "pro",
        "merchant_consistent": True,
    },
    {
        "amount": 42.5,
        "payment_status": "unknown",
        "chargeback": False,
        "customer_tier": "pro",
        "merchant_consistent": True,
    },
    {
        "amount": 4800.0,
        "payment_status": "settled",
        "chargeback": True,
        "customer_tier": "pro",
        "merchant_consistent": True,
    },
]
# Low-frequency enterprise case (~5%): deliberately below the calibration support
# bar, so it keeps falling back and exercises conservative coverage honestly.
RARE_CASE = {
    "amount": 95.0,
    "payment_status": "settled",
    "chargeback": False,
    "customer_tier": "enterprise",
    "merchant_consistent": True,
}


def pick_case(i):
    if i % 20 == 19:
        return dict(RARE_CASE)
    return dict(CASES[i % len(CASES)])
REQUIREMENTS = PromotionRequirements(
    min_samples=100,
    min_quality=0.85,
    min_confidence=0.85,
    max_degradation=0.2,
    comparison_rate=0.3,
    min_region_samples=100,
    evaluation_window=600,
    max_uncovered_rate=0.5,
)


class Ledger:
    def __init__(self, path=":memory:", limit=100):
        self.limit = limit
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS actions "
            "(id INTEGER PRIMARY KEY, state TEXT, choice TEXT, balance REAL, quality REAL)"
        )

    def execute(self, state, choice):
        """Apply the action and check ledger invariants, independently of its proposer."""
        eligible = (
            state["payment_status"] == "settled"
            and not state["chargeback"]
            and state["merchant_consistent"]
            and state["amount"] <= self.limit
        )
        needs_info = (
            state["payment_status"] == "unknown"
            and not state["chargeback"]
            and state["merchant_consistent"]
            and state["amount"] <= self.limit
        )
        balance = -state["amount"] if choice == "refund" else 0.0
        quality = float(
            (choice == "refund" and eligible)
            or (choice == "request_information" and needs_info)
            or (choice == "specialist" and not eligible and not needs_info)
        )
        cursor = self.db.execute(
            "INSERT INTO actions(state,choice,balance,quality) VALUES (?,?,?,?)",
            (json.dumps(state, sort_keys=True), choice, balance, quality),
        )
        self.db.commit()
        return Outcome(
            quality,
            "refund-ledger",
            "1",
            {
                "entry": cursor.lastrowid,
                "balance_delta": balance,
                "refund_limit": self.limit,
                "eligible": eligible,
                "information_required": needs_info,
            },
        )

    def close(self):
        self.db.close()


def verify(state, choice):
    ledger = Ledger()
    try:
        return ledger.execute(state, choice)
    finally:
        ledger.close()


def rules(state, limit=100):
    """Offline test fixture, never represented as a frontier model."""
    if state["chargeback"] or not state["merchant_consistent"] or state["amount"] > limit:
        return FallbackResult("specialist", model_calls=0, provider="fixture", model="rules")
    return FallbackResult(
        "refund" if state["payment_status"] == "settled" else "request_information",
        model_calls=0,
        provider="fixture",
        model="rules",
    )


def model_fallback(state, model, limit=100):
    """One real OpenAI-compatible request; usage must be returned by the provider.

    Transient 429/5xx responses retry with backoff (honouring Retry-After);
    persistent failures raise and abort the run visibly. Nothing is fabricated.
    """
    key = os.environ.get("MICROLOOP_API_KEY")
    if not key:
        raise RuntimeError("Set MICROLOOP_API_KEY for the real-model experiment")
    base = os.environ.get("MICROLOOP_API_BASE", "https://api.openai.com/v1").rstrip("/")
    prompt = (
        f"Choose one action: refund, request_information, specialist. Refund only settled "
        f"payments up to {limit} with no chargeback and consistent merchant settings. "
        "Request information for unknown payment status when other refund conditions pass. "
        "Otherwise choose specialist. Return JSON with only a choice field."
    )
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(state)},
            ],
            "response_format": {"type": "json_object"},
        }
    ).encode()
    last = None
    for attempt in range(10):
        request = urllib.request.Request(
            base + "/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                # Some providers WAF-block the default urllib user agent.
                "User-Agent": "microloop-refund-agent/0.4.0",
            },
            data=payload,
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                result = json.load(response)
            break
        except urllib.error.HTTPError as error:
            last = error
            if error.code not in (429, 500, 502, 503) or attempt == 9:
                raise
            retry_after = error.headers.get("Retry-After")
            time.sleep(float(retry_after) if retry_after else 2.0 * (attempt + 1))
    else:
        raise last
    choice = json.loads(result["choices"][0]["message"]["content"])["choice"]
    usage = result.get("usage", {})
    return FallbackResult(
        choice,
        model_calls=1,
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        provider=base,
        model=model,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--engine", choices=["exact", "laya"], default="exact")
    parser.add_argument("--checkpoint")
    parser.add_argument("--model", help="Real fallback model; omission selects a labelled fixture")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    db = args.output / "decisions.db"
    if db.exists():
        parser.error("Choose a fresh output directory; experiments never overwrite history")
    if args.model and not os.environ.get("MICROLOOP_API_KEY"):
        parser.error("Real-model runs require MICROLOOP_API_KEY; no calls were made")
    ledger = Ledger(args.output / "ledger.db")
    report = {
        "provenance": "generated operational cases",
        "engine": args.engine,
        "fallback": args.model or "deterministic test fixture (not a model)",
        "hardware": platform.platform(),
        "requirements": asdict(REQUIREMENTS),
        "phases": {},
        "production_evidence": False,
    }
    start = time.perf_counter()
    with Microloop(db, engines=[LayaEngine(args.checkpoint)]) as client:

        def batch(name, count, limit=100, novel=False):
            ledger.limit = limit
            sources, elapsed = {}, []
            success = 0
            for i in range(count):
                state = pick_case(i)
                if novel:
                    state["amount"] = 9000.0 + i
                    state["merchant_consistent"] = False
                t = time.perf_counter()

                def fallback(state=state):
                    return (
                        model_fallback(state, args.model, limit)
                        if args.model
                        else rules(state, limit)
                    )

                result = client.decide(
                    site=SITE, state=state, task_id=f"{name}-{i}", fallback=fallback
                )
                elapsed.append(time.perf_counter() - t)
                outcome = ledger.execute(state, result.choice)
                client.record_outcome(result.decision_id, **asdict(outcome))
                success += outcome.quality
                sources[result.source] = sources.get(result.source, 0) + 1
            phase = {
                "decisions": count,
                "sources": sources,
                "quality": success / count,
                "median_seconds": sorted(elapsed)[len(elapsed) // 2],
            }
            report["phases"][name] = phase
            print(json.dumps({name: phase}), flush=True)

        batch("observe", 3000)
        client.compile(SITE, engine=args.engine)
        try:
            client.calibrate(SITE, verifier=verify, requirements=REQUIREMENTS)
            batch("shadow", 600)
            evidence = client.evaluate(SITE, verifier=verify)
            report["promotion"] = {k: v for k, v in evidence.items() if not k.endswith("records")}
            if evidence["qualified"]:
                batch("active", 600)
                report["steady_evaluation"] = client.reevaluate(SITE)
                batch("novel", 6, novel=True)
                batch("drift", 600, limit=10)
                report["drift_evaluation"] = client.reevaluate(SITE)
                batch("after_demotion", 3, limit=10)
        except ValueError as error:
            report["qualification_blocker"] = str(error)
        report["site"] = client.inspect(SITE)
        client.store.export(args.output / "history.json")
    ledger.close()
    report["elapsed_seconds"] = time.perf_counter() - start
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {"report": str(args.output / "report.json"), "final_state": report["site"]["state"]}
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
