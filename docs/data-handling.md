# Data Handling

## Data Stored
Ink stores the following strictly for internal operation and evaluation:
- **Decision States:** Contextual data fed into the decision process.
- **Choices:** The set of available bounded choices at the decision site.
- **Outcomes:** The recorded outcome data used to qualify decisions.
- **Artifact Payloads:** Serialized decision logic and bounds.
- **Coverage Maps:** Telemetry on which paths are observed, shadowed, and active.

## Storage Location
All data is stored locally in an embedded SQLite WAL database.

## Data Egress & Network Access
- **Decision & Telemetry Data:** Ink sends no decision-state, outcome, prompt, analytics, or telemetry data off the host machine. All decision execution, training, calibration, and storage remain strictly in-process and local.
- **Model Provisioning:** When using the default internal learned model (`ink-decision-v1`), model weights (~807 MB) may be downloaded from the configured model repository (HuggingFace Hub) during initial provisioning if not already present in the local cache (`~/.cache/ink/models/decision-v1/`).
- **Air-Gapped & Offline Isolation:** For environments where all outbound network access is prohibited:
  - Model weights can be pre-installed into the image via `ink model-install` before deployment.
  - Or pointed to an existing local directory via `export INK_MODEL_DIR=/path/to/weights`.
  - For debugging or testing without model weights, the internal switch `INK_MODEL_DISABLED=1` restricts operation to the exact tier only.

## Data Retention and Compaction
Data retention policies are controlled locally. You can prune old decision records while preserving active qualification evidence using either the Python API or CLI:

```python
# In Python: prune decisions older than 30 days across all sites
loop.compact(max_age_days=30, vacuum=True)

# Or for a specific site
loop.compact("customer_support.route", max_age_days=14)
```

```bash
# In shell / cron:
ink retain --days 30
ink retain customer_support.route --days 14
```

Deleting the database file removes all stored states, traces, and artifacts. There is no remote backup managed by Ink.

## PII and Sensitive Data Best Practices
States passed into `decide(state=...)` are stored in local SQLite records.
- Pass only bounded, categorical, or extracted feature fields needed for decision-making (e.g. `tier`, `category`, `status`).
- Do not pass raw passwords, credit cards, or unnecessary personal identifying information (PII) in state dictionaries.
- If raw text must be vectorized, consider hashing or sanitizing identifiers before passing them into `state`.

## Emergency Kill Switches & Fallbacks
If an incident occurs or fast-path execution needs to be immediately disabled:

1. **In Code (Soft Kill-Switch)**:
   ```python
   # Immediately bypasses local fast paths, executes fallback, logs diagnostic traces
   loop = Ink(disable_fast_path=True)
   ```
2. **Environment Variable (Zero-Deployment Bypass)**:
   ```bash
   # Soft kill-switch: fast path bypassed, fallback executed, records diagnostics
   export INK_DISABLE_FAST_PATH=1

   # Hard kill-switch: completely bypasses Ink logic, zero SQLite operations
   export INK_DISABLED=1
   ```
3. **Programmatic Invalidation**:
   ```python
   loop.invalidate("customer_support.route", action="demote")  # returns site to SHADOW
   ```

## GDPR Considerations
Since all data remains on the host machine and is fully controlled by the user's infrastructure, GDPR compliance falls within the application's existing data handling boundaries. Ink introduces zero third-party data processors and performs zero cloud telemetry.
