# CLI Reference

Ink includes a command-line interface for inspecting DecisionSites, compiling Fast Paths, running maintenance, and monitoring system health.

---

## Commands Summary

```bash
# View registered DecisionSites
ink sites [--db PATH] [--json]

# Inspect a specific DecisionSite
ink inspect <site_name> [--db PATH] [--json]

# Launch the local web observability console
ink console [--port 8000] [--host 127.0.0.1]

# Check database and runtime health
ink doctor [--db PATH] [--json]

# Compile candidate Fast Paths from verified outcomes
ink compile <site_name> [--db PATH]

# Run maintenance cycle (re-evaluate shadow candidates and demote drifted paths)
ink maintenance [--db PATH]

# Discover candidate DecisionSites from trace logs
ink discover <traces.jsonl>
```

---

## Command Reference

### `ink sites`
Lists all DecisionSites registered in the local database.

Options:
- `--db PATH`: Path to SQLite database (default: `.ink/decisions.db`).
- `--json`: Output as JSON.

---

### `ink inspect <site_name>`
Shows detailed status, observation count, active artifact, and qualification state for a DecisionSite.

```bash
ink inspect support.route
```

---

### `ink console`
Launches a local-first HTTP dashboard on port 8000 for monitoring Fast Path hit rates, latency savings, and decision streams.

```bash
ink console --port 8000
```

---

### `ink doctor`
Inspects database integrity, schema version, registered sites, and neural runtime availability.

```bash
ink doctor
```

---

### `ink compile <site_name>`
Compiles observed verified decisions into candidate Fast Path artifacts (`Exact` or `Linear`).

---

### `ink maintenance`
Runs periodic maintenance across all DecisionSites. Tests candidates against qualification thresholds and demotes drifted artifacts back to the Host model.

---

### `ink discover <traces.jsonl>`
Analyzes agent trace logs to identify repeated bounded decisions and estimate cost savings.
