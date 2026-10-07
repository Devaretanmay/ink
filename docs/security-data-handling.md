# Security and Data Handling

A clear, technical explanation of what Ink stores, what it transmits, and what remains on your infrastructure.

---

## What Ink Stores

Ink stores runtime state in a local SQLite database (by default at `.ink/decisions.db`).

### Stored Fields

| Table | What is stored | Purpose |
| :--- | :--- | :--- |
| `sites` | Site name, version hash, canonical contract JSON, creation timestamp | DecisionSite registration |
| `decisions` | Decision ID, site version, task ID, timestamp, canonical state JSON, choice, source (`fast_path`/`fallback`), reason, fast-path version, prediction JSON, confidence, latency, fallback usage JSON | Audit trail and evidence collection |
| `outcomes` | Decision ID, quality score (0.0–1.0), verifier name, verifier version, evidence JSON, timestamp | Qualification and drift detection |
| `artifacts` | Artifact ID, site version, engine payload (weights/table), checksum, profile JSON, evidence JSON, status (`CANDIDATE`/`ACTIVE`/`RETIRED`), epoch | Compiled Fast Path definitions |
| `state_coverage` | Site version, canonical state JSON, observation count, fast-served count | Coverage boundaries |
| `drift_checks` | Artifact ID, timestamp, quality delta, demotion flag, missing outcomes count | Drift monitoring history |

### What is NOT Stored by Ink Core

Unless explicitly passed as part of the `state` dict by the host application:
- **Raw prompts** are not extracted or stored separately
- **Full conversation history** is not captured
- **Provider API keys** are never accessed, requested, or stored
- **Complete agent execution traces** are not retained
- **User credentials** are never handled

> [!WARNING]
> **State Content:** Whatever dictionary you pass to `state` in `decide()` is stored in SQLite in canonical JSON format. If your `state` includes raw PII, user emails, or full message bodies, those will reside in the SQLite database. Keep state minimal: declare only the fields the decision actually needs, hash or tokenize identifiers before they reach `state`, and never pass credentials. Whatever you declare in `state_schema` is exactly what gets stored.

---

## Network Behavior

| Operation | Network Activity | Notes |
| :--- | :--- | :--- |
| `decide()` | **Zero network calls** | Executes entirely in-process. Local Fast Paths run via ExactEngine or MLX; fallback executes your own Python callable. |
| `record_outcome()` | **Zero network calls** | Writes directly to local SQLite. |
| `ink discover` | **Zero network calls** | Reads local JSONL files only. |
| `ink sites`, `ink status`, `ink inspect` | **Zero network calls** | Inspects local SQLite only. |
| `ink model-install` | **Downloads public weights** | Downloads fine-tuned weights (~807 MB) from HuggingFace on first initialization if not pre-installed. |

**Telemetry:** Ink contains **zero phone-home, usage tracking, or analytics telemetry**. No data leaves your machine or container during runtime operation.

---

## Storage Location and Configuration

By default, the database is placed at `.ink/decisions.db` relative to the working directory.

You can configure the location explicitly:

```python
from ink import Ink

# Custom path (e.g. on a dedicated volume or in-memory)
with Ink(path="/var/data/my-agent/decisions.db") as engine:
    ...
```

For testing or ephemeral workloads:

```python
with Ink(path=":memory:") as engine:
    ...
```

---

## Data Retention and Deletion

### CLI Retention Management

```bash
# Delete decision records older than 30 days and VACUUM the database
ink retain --days 30 --db .ink/decisions.db

# Delete records before a specific Unix timestamp
ink retain --before 1727712000 --db .ink/decisions.db

# Export the database for compliance or audit review
ink export /path/to/backup.json --db .ink/decisions.db
```

### Complete Deletion

To completely remove all stored data:

```bash
rm -rf .ink/decisions.db*
```

This removes the SQLite database and all write-ahead log (`-wal`) and shared memory (`-shm`) files. The next invocation will recreate a clean database.
