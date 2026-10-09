# DecisionSites: Contracts & Design Guide

A `DecisionSite` defines an explicit, versioned, bounded semantic decision boundary in your AI software.

---

## Anatomy of a DecisionSite

```python
from ink import DecisionSite

site = DecisionSite(
    name="workflow.approval_gate",
    state_schema={
        "user_tier": "string",
        "requested_amount": "number",
        "kyc_verified": "boolean",
        "notes": "string?",  # Optional string
    },
    choices=("auto_approve", "manual_review", "reject"),
    fallback_revision="1",
    description="Risk gate evaluating automated micro-loan disbursement",
)
```

### Parameters

1. **`name` (str):** Unique hierarchical identifier for the decision site (e.g., `namespace.action`).
2. **`state_schema` (dict[str, str]):** Strongly typed schema mapping field names to data types:
   - `"string"` / `"string?"` (nullable)
   - `"integer"` / `"integer?"` (nullable)
   - `"number"` / `"number?"` (nullable)
   - `"boolean"` / `"boolean?"` (nullable)
3. **`choices` (tuple[str, ...]):** Sequence of at least two unique string labels representing discrete choices.
4. **`fallback_revision` (str, default `"1"`):** Version string of the host model prompt/logic. Bumping this revision signals that host behavior changed, gracefully triggering re-calibration.
5. **`description` (str, optional):** Human-readable documentation for developers and observability dashboards. Editing `description` does not alter the underlying contract hash.

---

## Contract Hashing & Isolation

Every `DecisionSite` has an immutable cryptographic hash computed from its canonical definition:

```python
print(site.version)
# e.g., 'a98f12cba04ef...'
```

- **Zero Cross-Talk:** Evidence, model weights, and calibration profiles are partitioned strictly by `site.version`.
- **Schema Safety:** If fields or choices change, a new version is created automatically, preventing silent schema corruption.

---

## Designing Effective Decision Boundaries

### Rule 1: Keep State Bounded and Relevant
Only include fields that directly inform the choice. Strip transient timestamps, request UUIDs, or random tokens that would create false entropy.

### Rule 2: Choices Must Be Mutually Exclusive
Ensure the declared choices cover all possible operational paths at this node.

### Rule 3: Prefer Primitive Types
Flatten nested JSON objects into primitive fields (strings, numbers, booleans) before passing state to Ink.
