# Deployment Guide

Ink is an embedded local runtime. It runs inside your application process without requiring external microservices or vector databases.

---

## 1. Storage Architecture

Ink stores decisions, verified outcomes, and compiled artifacts in a local SQLite database:

```python
ink = Ink(".ink/decisions.db")
```

### SQLite Engine Settings
- Ink enables Write-Ahead Logging (`PRAGMA journal_mode = WAL`) automatically.
- Readers do not block writers.
- High-concurrency applications achieve up to thousands of local reads per second per core.

---

## 2. Process Concurrency & Background Maintenance

Ink maintains Fast Paths in two modes:

### Mode A: In-Process Auto-Maintenance (Default for Single Instances)
```python
ink = Ink(
    ".ink/decisions.db",
    auto_maintenance=True,
    maintenance_interval=60.0,  # runs compiler every 60 seconds
)
```

### Mode B: External Maintenance Worker (Recommended for Production Fleets)
Disable in-process auto-maintenance on serving nodes and run a dedicated background maintenance process:

```python
# Serving API pods
ink = Ink(".ink/decisions.db", auto_maintenance=False)
```

```bash
# Periodic cron or sidecar worker
ink maintenance --db /shared/decisions.db --interval 60
```

---

## 3. Horizontal Scaling & Multi-Node Fleets

For stateless container fleets (Kubernetes, AWS ECS, Fly.io):

1. **Shared Volume**:
   Mount a high-performance shared volume (NFS, EFS, NVMe shared block) containing `decisions.db`.
2. **Local Replica Sync**:
   Export qualified artifacts from a primary node and distribute read-only Fast Paths to edge workers:
   ```bash
   ink export --site support.route --output artifact.json
   ```

---

## 4. Health Checks & Diagnostics

Verify runtime health in CI/CD and container entrypoints:

```bash
ink doctor --db .ink/decisions.db
```

Output:
```json
{
  "status": "healthy",
  "integrity": "ok",
  "schema_version": 6,
  "sites_count": 3,
  "active_artifacts_count": 2,
  "neural_available": true
}
```
