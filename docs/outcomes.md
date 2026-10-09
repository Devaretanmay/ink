# Outcomes & Ground Truth Verification

In Ink, **confidence does not grant authority; evidence grants authority**.

A Fast Path cannot be compiled or served until independent real-world outcomes prove that the candidate implementation performs reliably.

---

## What is an Outcome?

An `Outcome` is an immutable record verifying the quality of a prior decision:

```python
from ink import Outcome

outcome = Outcome(
    quality=1.0,  # Float in [0.0, 1.0] representing success/correctness
    verifier="csat_survey_processor",  # Identifier of the verification system
    verifier_version="v2",  # Version of the verification logic
    evidence={  # Arbitrary serializable metadata validating the outcome
        "rating": 5,
        "resolved_first_contact": True,
    },
)
```

---

## Recording Outcomes

Outcomes are recorded asynchronously using the unique `decision_id` returned by `ink.decide()`:

### Style 1: Explicit Outcome Object
```python
ink.record_outcome(decision_id, outcome)
```

### Style 2: Keyword Arguments
```python
ink.record_outcome(
    decision_id,
    quality=1.0,
    verifier="automated_integration_test",
    verifier_version="v1",
    evidence={"exit_code": 0},
)
```

---

## Designing Verifiers

A verifier should reflect the true downstream objective of your system:

| Decision Domain | Ideal Verifier Source | Quality Metric |
| :--- | :--- | :--- |
| **Customer Support Triage** | Did the user accept the ticket category or request re-routing? | 1.0 if accepted, 0.0 if re-assigned |
| **Agent Tool Selection** | Did the tool invocation succeed without throwing an exception? | 1.0 if exit code 0, 0.0 on tool error |
| **Transaction Approval** | Was the transaction flagged as fraudulent in the subsequent 30 days? | 1.0 if legitimate, 0.0 if charged back |
| **Coding Agent Action** | Did the unit test suite pass on the generated code branch? | Test pass rate (e.g. 1.0 or 0.0) |

---

## Verification Partitions

When compiling candidate engines, Ink enforces strict temporal data splitting:
1. **Train Partition (60%):** Used to fit representations and candidate model parameters.
2. **Calibration Partition (20%):** Used to compute initial confidence intervals and define coverage boundaries.
3. **Shadow Evaluation Partition (20%):** Used strictly for holdout verification to prove generalization before granting serving authority.
