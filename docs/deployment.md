# Deployment guide

Ink is an embedded local runtime. It runs inside your application process without requiring external microservices, vector databases, or background cloud infrastructure.

---

## 1. Storage architecture

Ink stores decisions, verified outcomes, and compiled artifacts in a local SQLite database:

```python
ink = Ink(".ink/decisions.db")
```

### SQLite engine settings
- Ink enables Write-Ahead Logging (`PRAGMA journal_mode = WAL`) automatically.
- Readers never block writers, and writers never block readers.
- High-concurrency applications achieve thousands of local reads per second per core.

---

## 2. Process concurrency & maintenance

Ink maintains Fast Paths in two modes:

### Mode A: In-process auto-maintenance (great for single instances and local dev)
```python
ink = Ink(
    ".ink/decisions.db",
    auto_maintenance=True,
    maintenance_interval=60.0,  # runs compiler every 60 seconds in background thread
)
```

### Mode B: External maintenance worker (recommended for production fleets)
Disable in-process auto-maintenance on serving API nodes and run a dedicated background maintenance process:

```python
# In your API web pods (FastAPI, Flask, etc.)
ink = Ink(".ink/decisions.db", auto_maintenance=False)
```

```bash
# In a periodic cron job or sidecar container
ink maintenance --db /shared/decisions.db
```

---

## 3. Horizontal scaling & multi-node fleets

For stateless container fleets (Kubernetes, AWS ECS, Fly.io):

1. **Shared volume:**
   Mount a high-performance shared volume (NFS, EFS, NVMe shared block) containing `decisions.db`.
2. **Local replica sync:**
   Export qualified artifacts from a primary node and distribute read-only Fast Paths to edge workers:
   ```bash
   ink export --site support.route --output artifact.json
   ```

---

## 4. Health checks & diagnostics

Verify runtime health in CI/CD pipelines and container entrypoints:

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
