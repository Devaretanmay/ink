# Microloop CLI Reference

Microloop includes a command-line utility for inspecting sites, profiling economics, compiling candidates, running maintenance, and managing models.

```bash
# View all registered decision sites
microloop sites [--db PATH] [--json]

# Inspect site status, active artifact, and economic profile
microloop inspect <site_name> [--db PATH] [--json]

# Discover candidate sites from agent execution logs
microloop discover <traces_path>

# Compile a candidate fast path from observed history
microloop compile <site_name> [--replace] [--db PATH]

# Evaluate and promote shadow candidates against holdout evidence
microloop evaluate <site_name> --verifier module:callable --requirements requirements.json [--db PATH]

# Run fleet maintenance (demote drifted paths, requalify shadow candidates)
microloop maintenance [--verifier module:callable] [--requirements requirements.json] [--db PATH]

# Export decision database snapshot for auditing
microloop export <output_path> [--db PATH]

# Prune historical decisions while preserving audit evidence
microloop retain [<site_name>] --before <unix_timestamp> [--db PATH]

# Install Microloop Decision Model v1 weights locally
microloop model-install [--checkpoint PATH]

# Fine-tune Microloop Decision Model heads on labeled observations
microloop model-train --data rows.jsonl --output <output_dir> [--steps N] [--lr LR] [--seed S]
```

---

## Command Reference

### `microloop sites`
Lists all decision sites registered in the local SQLite database.
- `--db PATH`: Path to decision database (default: `.microloop/decisions.db`).
- `--json`: Output as JSON array.

```bash
$ microloop sites
support.route  ACTIVE
agent.tool     OBSERVE
```

---

### `microloop inspect <site_name>`
Displays full diagnostics for a site, including:
- Current lifecycle state (`OBSERVE`, `SHADOW`, `ACTIVE`).
- Active fast-path artifact hash.
- Total observations, model calls, and cost.
- Advisory economic profile (`SiteProfile`) with repetition rate, choice entropy, and break-even horizon.

```bash
$ microloop inspect support.route --json
```

---

### `microloop discover <traces_path>`
Analyzes raw agent execution logs (JSON or JSONL formatted from browser-use, LangGraph, OpenAI, or Anthropic traces). Outputs a ranked list of candidate DecisionSites with:
- `repetition_rate`: Exact input repeat rate.
- `output_entropy`: Choice distribution entropy in bits.
- `verifiability`: Ground-truth verification signal quality.
- `estimated_annual_savings`: Projected net annual cost reduction in USD.
- `recommendation`: `"compile"`, `"investigate"`, or `"ignore"`.

```bash
$ microloop discover agent_traces.jsonl
```

---

### `microloop compile <site_name>`
Fits candidate local fast-path parameters from observed fallback history with outcomes:
- Splits history chronologically into train (60%), calibration (20%), and evaluation (20%) partitions.
- Fits exact hash table or neural decision heads.
- Creates an uncalibrated `SHADOW` artifact.
- `--replace`: Retires any existing candidate and recompiles from scratch.

---

### `microloop evaluate <site_name>`
Evaluates a shadow artifact against holdout outcomes and fresh shadow evidence:
- `--verifier module:callable`: Python import path to the independent verifier.
- `--requirements requirements.json`: JSON file specifying `PromotionRequirements` (`min_samples`, `min_quality`, `max_degradation`, `comparison_rate`).
- If all statistical tests pass under one-sided Hoeffding bounds, the artifact is atomically promoted to `ACTIVE`.

---

### `microloop maintenance`
Runs autonomous fleet maintenance across all registered sites:
- Checks comparison traffic for business policy drift.
- Demotes drifted active artifacts back to `SHADOW`.
- Re-evaluates shadow candidates on fresh evidence and promotes when ready.
- Runs non-blockingly and exits immediately. Schedule via cron or host application background workers.

---

### `microloop export <output_path>`
Generates a consistent, versioned JSON snapshot of all sites, decisions, outcomes, artifacts, and lifecycle events for offline auditing or migration.

---

### `microloop retain [<site_name>] --before <unix_timestamp>`
Prunes historical raw decision rows older than the specified timestamp to bound disk usage:
- Preserves all active artifacts, calibration profiles, and audit event logs.
- Executes `VACUUM` to reclaim disk space.

---

### `microloop model-install`
Installs the embedded weights for `microloop-decision-v1` into `~/.cache/microloop/models/decision-v1`:
- Verifies SHA-256 integrity of model files.
- Downloads once; runs completely offline thereafter.

---

### `microloop model-train`
Fine-tunes the decision heads and scorer of Microloop Decision Model v1 on labeled historical decision rows:
- `--data rows.jsonl`: Path to JSONL file containing state/choices/choice records.
- `--output <dir>`: Directory where the fine-tuned checkpoint and updated model card are written.
