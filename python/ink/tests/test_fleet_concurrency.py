"""Multi-site isolation, bounded fleet maintenance, health reporting, and concurrency."""

import queue
import threading
import time
from dataclasses import asdict

from ink import DecisionSite, FallbackResult, Ink, Outcome, PromotionRequirements

SITE_A = DecisionSite("support.route", {"tier": "string"}, ("billing", "tech"))
SITE_B = DecisionSite("refund.action", {"amount": "number"}, ("approve", "escalate"))
REQ = PromotionRequirements(10, 0.5, 0.5, 0.6, 0.25, 5, 100)


def verify_a(state, choice):
    exp = "billing" if state["tier"] == "enterprise" else "tech"
    return Outcome(float(choice == exp), "verifier_a", "1", {"expected": exp})


def verify_b(state, choice):
    exp = "approve" if state["amount"] < 50.0 else "escalate"
    return Outcome(float(choice == exp), "verifier_b", "1", {"expected": exp})


def seed_site(client, site, verifier, count=150, prefix="task"):
    for i in range(count):
        if site == SITE_A:
            state = {"tier": "enterprise" if i % 2 == 0 else "standard"}
            exp = "billing" if state["tier"] == "enterprise" else "tech"
        else:
            state = {"amount": 25.0 if i % 2 == 0 else 75.0}
            exp = "approve" if state["amount"] < 50.0 else "escalate"
        res = client.decide(
            site=site,
            state=state,
            task_id=f"{prefix}-{site.name}-{i}",
            fallback=lambda exp=exp: FallbackResult(exp, model_calls=1),
        )
        out = verifier(state, res.choice)
        client.record_outcome(res.decision_id, **asdict(out))


def activate_site(client, site, verifier):
    seed_site(client, site, verifier, 300, prefix="obs")
    client.compile(site, engine="exact")
    client.calibrate(site, verifier=verifier, requirements=REQ)
    seed_site(client, site, verifier, 100, prefix="shadow")
    eval_res = client.evaluate(site, verifier=verifier)
    assert eval_res["qualified"]


def test_multi_site_isolation(tmp_path):
    with Ink(tmp_path / "fleet.db") as client:
        activate_site(client, SITE_A, verify_a)
        seed_site(client, SITE_B, verify_b, 50, prefix="obs")

        insp_a = client.inspect(SITE_A)
        insp_b = client.inspect(SITE_B)
        assert insp_a["state"] == "ACTIVE"
        assert insp_b["state"] == "OBSERVE"
        assert insp_a["fast_path"] is not None
        assert insp_b["fast_path"] is None

        reeval = client.reevaluate(SITE_A)
        assert not reeval["demoted"]
        assert client.inspect(SITE_B)["state"] == "OBSERVE"


def test_decision_receipts(tmp_path):
    with Ink(tmp_path / "receipts.db") as client:
        activate_site(client, SITE_A, verify_a)

        served_fast = None
        for _ in range(20):
            res = client.decide(
                site=SITE_A,
                state={"tier": "enterprise"},
                fallback=lambda: "billing",
            )
            if res.source == "fast_path":
                served_fast = res
                break
        assert served_fast is not None
        receipt = served_fast.receipt
        assert receipt["site"] == SITE_A.name
        assert receipt["served_by"] == "fast_path"
        assert receipt["verification_status"] == "verified"
        assert receipt["representation_version"] == "exact_v1"
        assert receipt["distance"] == 0.0

        stored_receipt = client.receipt(served_fast.decision_id)
        assert stored_receipt["served_by"] == "fast_path"
        assert stored_receipt["verification_status"] == "verified"
        assert stored_receipt["site"] == SITE_A.name

        res_unseen = client.decide(
            site=SITE_A,
            state={"tier": "vip"},
            fallback=lambda: "tech",
        )
        assert res_unseen.receipt["served_by"] == "fallback"
        assert res_unseen.receipt["verification_status"] == "fallback"


def test_bounded_maintenance_and_prioritization(tmp_path):
    with Ink(tmp_path / "maint.db") as client:
        activate_site(client, SITE_A, verify_a)
        seed_site(client, SITE_B, verify_b, 50, prefix="obs")

        bounded = client.maintenance(max_sites=1)
        assert len(bounded) == 2
        assert SITE_A.name in bounded
        assert bounded[SITE_A.name].get("demoted") is False
        assert bounded[SITE_B.name] == {"deferred": "max_sites_reached"}

        timeout_res = client.maintenance(time_budget_sec=0.000001)
        assert any("deferred" in str(v) for v in timeout_res.values())


def test_fleet_health_reporting(tmp_path):
    with Ink(tmp_path / "health.db") as client:
        activate_site(client, SITE_A, verify_a)
        seed_site(client, SITE_B, verify_b, 20, prefix="obs")

        h_a = client.health(SITE_A)
        assert h_a["name"] == SITE_A.name
        assert h_a["status"] == "ACTIVE"
        assert h_a["false_serves"] == 0

        fleet = client.fleet_health()
        assert fleet["fleet_size"] == 2
        assert fleet["summary"]["active_sites"] == 1
        assert fleet["summary"]["observe_sites"] == 1
        assert fleet["summary"]["total_observations"] == 420


def test_concurrent_decide_and_maintenance(tmp_path):
    db_path = tmp_path / "concurrency.db"
    with Ink(db_path) as client:
        activate_site(client, SITE_A, verify_a)
        seed_site(client, SITE_B, verify_b, 50, prefix="obs")

    stop_event = threading.Event()
    outcome_q = queue.Queue()
    errors = []

    def worker_decide(site, choices, count):
        try:
            with Ink(db_path) as cl:
                for _ in range(count):
                    st = {"tier": "enterprise"} if site == SITE_A else {"amount": 20.0}
                    exp = choices[0]
                    res = cl.decide(site=site, state=st, fallback=lambda exp=exp: exp)
                    outcome_q.put((res.decision_id, site, st, res.choice))
                    time.sleep(0.001)
        except Exception as e:
            errors.append(e)

    def worker_outcomes():
        try:
            with Ink(db_path) as cl:
                while not (stop_event.is_set() and outcome_q.empty()):
                    try:
                        dec_id, site, st, choice = outcome_q.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    v = verify_a if site == SITE_A else verify_b
                    out = v(st, choice)
                    cl.record_outcome(dec_id, **asdict(out))
                    outcome_q.task_done()
        except Exception as e:
            errors.append(e)

    def worker_maintenance(ticks=5):
        try:
            with Ink(db_path) as cl:
                for _ in range(ticks):
                    cl.maintenance(verifier=verify_b, requirements=REQ, engine="exact")
                    time.sleep(0.01)
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=worker_decide, args=(SITE_A, ("billing", "tech"), 40)),
        threading.Thread(target=worker_decide, args=(SITE_B, ("approve", "escalate"), 40)),
        threading.Thread(target=worker_outcomes),
        threading.Thread(target=worker_maintenance, args=(4,)),
    ]

    for t in threads:
        t.start()

    threads[0].join(timeout=10)
    threads[1].join(timeout=10)
    threads[3].join(timeout=10)
    stop_event.set()
    threads[2].join(timeout=10)

    assert not errors, f"Encountered concurrency errors: {errors}"

    with Ink(db_path) as cl:
        rows = cl.store.rows("PRAGMA integrity_check")
        assert rows[0]["integrity_check"] == "ok"
