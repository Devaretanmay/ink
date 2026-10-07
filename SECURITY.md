# Security Policy

## Supported versions

Only the latest release receives security fixes.

| Version | Supported |
|---|---|
| 0.6.x | yes |
| < 0.6 | no |

## Reporting a vulnerability

**Do not open a public issue.**

Use GitHub's private reporting, which creates an advisory only the maintainers can see:

**https://github.com/Devaretanmay/ink/security/advisories/new**

Please include:

- what the issue is, and what an attacker gains from it
- the version or commit you tested
- a minimal reproduction

You can expect an acknowledgement within a few days. If a fix is warranted it will ship in
a new patch release, and the advisory will credit you unless you prefer otherwise.

## What Ink does and does not do

Worth stating plainly, because it bounds the threat model:

- It runs **in process** inside your application. There is no network listener and no
  daemon.
- `decide()` and `record_outcome()` make **zero network calls**. All state is local.
- State is stored in a **local SQLite database** (default `.ink/decisions.db`). Whatever
  you place in `state` is written in canonical JSON, so keep declared fields minimal and
  free of raw credentials.
- It makes **no outbound calls during serving**. The one network operation is a
  **one-time download of public model weights** from HuggingFace on first learned use
  (skipped when you pre-provision with `ink model-install` or operate in exact-only mode).
- It **does not execute** agent actions, run shell commands, or call your models. It decides
  a candidate choice and returns it; your code executes the action.
- It **fails open**: a runtime or storage error routes the decision to your original
  fallback rather than failing the request.
- It has runtime dependencies (`numpy`, `tokenizers`, `mlx`, `huggingface-hub`). Vulnerable
  versions of those libraries are handled by normal dependency updates.
- Trace files and `--requirements` JSON are **untrusted input**: malformed records raise
  rather than being partially applied.

## Scope

In scope: the Python package, the CLI, the local decision model runtime, the SQLite decision
store, and the trace ingestion path.

Out of scope: issues in `benchmarks/` (a development harness, not shipped), and reports that
require an attacker to already control your process.
