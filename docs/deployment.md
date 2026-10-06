# Deployment Guide

How Ink runs in single-process applications, containerized services, and multi-replica environments.

---

## Deployment Architecture

Ink is an **in-process library**, not a background service, daemon, or hosted control plane.

```text
┌────────────────────────────────────────────────────────┐
│  Host Process (e.g. FastAPI, Celery, Agent Runner)     │
│                                                        │
│  from ink import Ink, DecisionSite                     │
│                                                        │
│  ┌───────────────────────┐   ┌──────────────────────┐  │
│  │   ExactEngine (RAM)   │   │  DecisionModel (MLX) │  │
│  └───────────────────────┘   └──────────────────────┘  │
│             │                            │             │
│             ▼                            ▼             │
│  ┌──────────────────────────────────────────────────┐  │
│  │     DecisionStore (Local SQLite with WAL mode)   │  │
│  │     Path: .ink/decisions.db                      │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────┘
```

---

## Deployment Topologies

### 1. Single-Process Python Application

The simplest and most common deployment:

- A single process runs your agent or service.
- Ink writes to a local SQLite database at `.ink/decisions.db`.
- SQLite operates in WAL mode, allowing concurrent reads while writes occur.
- **Recommended for:** CLI tools, local agents, single-instance web services.

### 2. Multi-Threaded Application (e.g. FastAPI / Gunicorn with threads)

- Multiple threads within the same Python process share a single `Ink` instance.
- Thread-safe: SQLite transactions and internal locks protect write operations.
- Exact engine reads are pure memory lookups.
- **Recommended for:** High-concurrency I/O-bound web services.

### 3. Containerized Service (Docker / Kubernetes)

```dockerfile
FROM python:3.12-slim

# Install Ink
RUN pip install --no-cache-dir ink-jit

# Pre-download decision model weights during build to prevent cold-start latency
RUN ink model-install

WORKDIR /app
COPY . .

# Persist decision database across container restarts
VOLUME ["/app/.ink"]

CMD ["python", "main.py"]
```

---

## Multi-Replica Limitations and Strategy

Technical buyers must understand how Ink operates across multiple container replicas or horizontally scaled pods:

> [!IMPORTANT]
> **No Distributed Fleet Coordination:** Ink does not include a distributed coordinator, Raft cluster, or centralized cloud control plane. State is purely local SQLite.

### Recommended Multi-Replica Architecture: Independent Local State

```text
Pod 1: [Agent Code] ──► Local SQLite (/var/ink/decisions.db) [Independent Lifecycle]
Pod 2: [Agent Code] ──► Local SQLite (/var/ink/decisions.db) [Independent Lifecycle]
Pod 3: [Agent Code] ──► Local SQLite (/var/ink/decisions.db) [Independent Lifecycle]
```

**How it works:**
- Each replica runs its own `Ink` instance with its own local SQLite database (on an ephemeral volume or emptyDir).
- Each replica independently observes, compiles, shadows, and qualifies Fast Paths.
- Replicas with identical traffic patterns will qualify the same decision sites at approximately the same rate.
- If one replica detects drift, it demotes locally; other replicas will detect the same drift independently as comparison traffic runs.

**Trade-offs:**
- **Qualification time:** Scales with traffic per replica (e.g., if total traffic is split 4 ways, each replica takes ~4x longer to reach qualification thresholds).
- **Simplicity:** Zero network configuration, no Redis/Postgres dependency, zero distributed failure modes.

### What NOT to Do with Multi-Replica

- ❌ **Do NOT share a single SQLite database across multiple containers via NFS / SMB / CIFS.** SQLite file locking over network filesystems is unreliable and prone to corruption under write concurrency.
- ❌ **Do NOT attempt to manually rsync the database between running instances.** Use `ink export` and import procedures if state transfer is required.

---

## Health Checks and Fail-Open Monitoring

Because Ink strictly fails open, an internal optimization error will not cause HTTP 500 responses. Monitor Ink's health through application logging:

```python
import logging

ink_logger = logging.getLogger("ink")
ink_logger.setLevel(logging.WARNING)

# Ink logs warnings when failing open:
# "Fast path routing error for site support.route: ...; failing open"
# "Storage contention/error for site support.route (...); failing open without durable record"
```

Use the CLI to check site health periodically in health probes:

```bash
ink status --db .ink/decisions.db --json
```
