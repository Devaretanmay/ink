# PII Guidance for DecisionSites

Decision state is persisted in a local SQLite database for qualification, coverage tracking, and drift detection. This guide explains how to prevent Personally Identifiable Information (PII) from entering Ink storage.

---

## 1. Keep DecisionSite State Minimal

A DecisionSite only needs the features required to make the decision — nothing more.

```python
# ❌ AVOID: Passing raw user messages or full context
state = {
    "user_email": "jane.doe@company.com",
    "full_message": "Hi, I ordered item #98723 on Tuesday and it arrived broken. My credit card is 4111...",
    "session_id": "sess_89a7fbc2",
}

# ✅ PREFERRED: Passing derived, categorical features
state = {
    "category": "damaged_goods",
    "amount_tier": "low",          # derived from amount < 50
    "has_receipt": True,
}
```

---

## 2. Derived Categorical Features Over Raw Text

Where possible, compute coarse categorical features in your host code before passing them to Ink:

| Raw Input (PII Risk) | Derived Categorical Feature |
| :--- | :--- |
| User email address (`jane@acme.com`) | Domain classification (`"enterprise"` vs. `"free"`) |
| Order total (`$47.82`) | Coarse bucket (`"<50"`, `"50-200"`, `">200"`) |
| Account ID / Customer ID | Tier (`"standard"`, `"vip"`, `"suspended"`) |
| Full street address | Region code (`"US-WEST"`, `"EU-CENTRAL"`) |
| Raw phone number | Country code only (`"+1"`, `"+44"`) |

Derived categories also **increase decision repetition**, helping Ink qualify Fast Paths significantly faster.

---

## 3. Hash or Tokenize Identifiers

If an identifier is strictly necessary to correlate downstream verification:

```python
import hashlib

def safe_identifier(raw_id: str, salt: str) -> str:
    """Produce a deterministic, one-way identifier."""
    return hashlib.sha256(f"{salt}:{raw_id}".encode()).hexdigest()[:16]

state = {
    "account_token": safe_identifier(user_id, salt="app-secret-salt"),
    "request_type": "password_reset",
}
```

---

## 4. Volatile Field Detection

When analyzing traces with `ink discover`, the profiler automatically detects volatile identifiers (UUIDs, timestamps, session IDs, auto-increment keys):

```text
2. agent.tool_select
   volatile fields: ['request_id', 'session_nonce']
     (suggested exclusions - developer review required)
```

The CLI will suggest excluding these fields in generated integration snippets:

```python
# Strip volatile non-decision fields before passing to decide():
clean_state = {k: v for k, v in raw_state.items() if k not in {"request_id", "session_nonce"}}
```

---

## 5. Storage Location and Retention Controls

- **Database Path:** Store `.ink/decisions.db` on an encrypted volume (e.g. dm-crypt/LUKS on Linux, FileVault on macOS) if your environment handles regulated data.
- **Data Pruning:** Regularly prune older decisions with `ink retain --days 30`.
- **Right to be Forgotten:** If an individual requests data erasure and their data may be in state fields, compact the database or delete `.ink/decisions.db` and allow Ink to re-observe from fresh traffic.
