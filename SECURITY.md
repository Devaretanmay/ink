# Security Policy

## Supported Versions

Only the latest minor release receives security fixes.

| Version | Supported |
| :--- | :--- |
| `0.6.x` | Yes |
| `< 0.6` | No |

---

## Reporting a Vulnerability

Do not open public GitHub issues for security vulnerabilities.

Use GitHub Private Vulnerability Reporting:
https://github.com/Devaretanmay/ink/security/advisories/new

Include:
- A description of the issue and potential impact.
- The version or commit tested.
- A minimal reproduction script.

Maintainers will acknowledge your report within 48 hours.

---

## Data Handling & Threat Model

Ink is an embedded local library.

- **Local-Only Execution**: `decide()` and `record_outcome()` make zero outbound network calls.
- **Data Storage**: Decisions, states, and verified outcomes persist in a local SQLite file (`.ink/decisions.db`). Declare only necessary fields in `state_schema` and exclude credentials or sensitive PII.
- **Network Operations**: The only outbound network action is a one-time download of public model weights on first use (skipped when weights are pre-installed with `ink model-install` or when using `ExactEngine`).
- **No Direct Action Execution**: Ink does not invoke external APIs or execute shell commands. It returns a selected choice string; your application executes the action.
- **Fail-Open Safety**: Any internal runtime or database failure immediately routes the request to your Host fallback model.
