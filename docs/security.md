# Security Documentation

## What data leaves the machine?
Nothing by default. All data stays local on the user's machine unless explicitly configured otherwise by the user.

## What is stored?
Decision states, possible choices, outcomes, artifact payloads, and coverage maps.

## Where is SQLite located?
Local SQLite WAL database, located by default in the `.microloop/` directory.

## What happens if Microloop crashes?
If Microloop crashes, the agent's fallback function handles the decision. There is no data loss and execution continues seamlessly.

## What happens if artifact integrity fails?
Artifact integrity is verified via a SHA-256 digest before every serve. If verification fails, the system safely falls back to the model.

## How do I disable it?
Remove the `client.decide()` wrapper around the relevant decision sites.

## How do I invalidate policy?
Call `client.invalidate(site)` to manually invalidate and force re-evaluation.

## How are outcomes recorded?
Outcomes are recorded explicitly by the application calling `client.record_outcome()`.

## What does ACTIVE mean?
ACTIVE means the fast path has passed shadow qualification against independent outcome verification and is now serving decisions locally.

## Can it execute business actions itself?
No. Microloop never executes business actions. It only selects which action the agent should take; the host agent executes it.
