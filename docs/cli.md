# CLI reference

Ink includes a command-line interface for inspecting DecisionSites, tracking accumulated evidence, running the local dashboard, compiling Fast Paths, and running health checks.

---

## Commands summary

```bash
# 1. Launch the local halftone dashboard on http://localhost:4242
ink ui [--port 4242] [--host 127.0.0.1] [--no-browser] [--allow-control-writes]

# 2. Discover potential DecisionSites from existing execution traces
ink discover <traces.jsonl> [--profile] [--snippet]

# 3. List registered DecisionSites and lifecycle status
ink sites [--db PATH] [--json]

# 4. Inspect detailed evidence and qualification metrics for a site
ink inspect <site_name> [--db PATH] [--json]

# 5. Compile candidate Fast Paths from verified outcomes
ink compile <site_name> [--engine auto|exact|linear] [--db PATH]

# 6. Evaluate candidate artifacts and test statistical qualification
ink evaluate <site_name> [--db PATH]

# 7. Run maintenance cycle (re-evaluate candidates and revoke drifted paths)
ink maintenance [--db PATH]

# 8. Check database integrity and runtime environment health
ink doctor [--db PATH] [--json]
```

---

## Detailed command reference

### `ink ui`
Launches the embedded local web console on `http://127.0.0.1:4242`.

```bash
ink ui
```

Options:
- `--port PORT`: Port to bind the console (default: `4242`).
- `--host HOST`: Host interface to bind (default: `127.0.0.1`).
- `--no-browser`: Do not open the browser automatically.
- `--allow-control-writes`: Enable state-changing serving control mutations (such as switching a site from `ACTIVE` to `DISABLED` via the UI). Disabled by default for localhost security.
- `--db PATH`: Path to SQLite database (default: `.ink/decisions.db`).

---

### `ink discover <traces.jsonl>`
Scans execution trace files (OpenTelemetry, LangSmith, LiteLLM, or raw JSONL) to identify repeated bounded decisions suitable for compilation.

```bash
ink discover traces.jsonl --profile
```

Flags:
- `--profile`: Computes repetition frequency, entropy, and estimated Host model call savings.
- `--snippet`: Generates ready-to-use Python integration code for discovered sites.

---

### `ink sites`
Lists all DecisionSites registered in the local evidence database, showing their current lifecycle state (`OBSERVE`, `SHADOW`, `ACTIVE`, `DEGRADED`).

```bash
ink sites
```

Options:
- `--db PATH`: Path to SQLite database (default: `.ink/decisions.db`).
- `--json`: Format output as JSON for programmatic integration.

---

### `ink inspect <site_name>`
Displays full telemetry, evidence depth, and statistical qualification status for a specific DecisionSite.

```bash
ink inspect agent.tool_choice
```

Output includes:
- **Site Status:** Lifecycle state (`OBSERVE`, `SHADOW`, `ACTIVE`).
- **Evidence Count:** Total recorded decisions and verified outcomes.
- **Active Engine:** Currently serving local artifact (`ExactEngine`, `LinearClassifierEngine`, or `none`).
- **Qualification Record:** Sample count ($n$), error count ($k$), point accuracy, and Wilson 95% confidence lower bound.
- **Serving Rates:** Historical Fast Path hit rate vs. Host fallback rate.

---

### `ink compile <site_name>`
Triggers the compiler plane to build candidate Fast Path artifacts from accumulated verified outcomes.

```bash
ink compile agent.tool_choice --engine auto
```

Options:
- `--engine`: Target serving engine (`auto`, `exact`, or `linear`). Defaults to `auto`.

---

### `ink evaluate <site_name>`
Evaluates the currently compiled candidate artifact against independent holdout evidence. If the candidate clears the site's error budget under the Wilson 95% confidence bound, it is promoted to `ACTIVE`.

```bash
ink evaluate agent.tool_choice
```

---

### `ink maintenance`
Executes an audit cycle across all registered DecisionSites:
1. Re-evaluates `SHADOW` candidates against new verification data.
2. Checks rolling accuracy on `ACTIVE` paths.
3. Automatically demotes drifted artifacts back to your Host model if error rates exceed the site budget.

```bash
ink maintenance
```

---

### `ink doctor`
Inspects database integrity, SQLite WAL status, registered sites count, and neural runtime availability.

```bash
ink doctor
```
