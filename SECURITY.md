# Security Policy

## Supported versions

Only the latest release receives security fixes.

| Version | Supported |
|---|---|
| 0.3.x | yes |
| < 0.3 | no |

## Reporting a vulnerability

**Do not open a public issue.**

Use GitHub's private reporting, which creates an advisory only the maintainers
can see:

**https://github.com/Devaretanmay/ink/security/advisories/new**

Please include:

- what the issue is, and what an attacker gains from it
- the version or commit you tested
- a minimal reproduction, ideally a trajectory or event sequence that triggers it

You can expect an acknowledgement within a few days. If a fix is warranted it
will ship in a new patch release, and the advisory will credit you unless you
prefer otherwise.

## What Ink does and does not do

Worth stating plainly, because it bounds the threat model:

- It runs **in process** inside your process. There is no network listener, no
  daemon, and no IPC.
- It makes **no network calls**. It cannot exfiltrate anything, because it has
  no way to reach the network.
- It has **no runtime Python dependencies**. A wheel install pulls in nothing
  else.
- It **never executes** anything. It reads `Event` values you construct and
  returns a `Decision`. It does not run shell commands, call models, or write
  files.
- It holds **bounded memory**: a fixed window of at most 4096 records, 32 by
  default. A long-running agent cannot grow its footprint.
- A trajectory file is untrusted input. Field lengths are bounded (`action` is
  rejected above 4096 bytes) and malformed or non-conforming records raise rather
  than being partially applied. Historical memory-safety defects in the
  underlying Rust dependency tree remain the relevant surface for a native
  extension.

## Scope

In scope: the Python package, the Rust crate, the CLI, and the trajectory parsing
path.

Out of scope: issues in `benchmarks/` (a development harness, not shipped), and
reports that require an attacker to already control your process.
