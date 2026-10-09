# Production Safety

Ink enforces strict statistical contracts before allowing any local artifact to serve traffic.

---

## Core Safety Invariants

1. **Confidence does not grant authority.**
   A model predicting high confidence on an input is not evidence of correctness. Ink ignores raw model confidence for serving authorization.

2. **Similarity does not grant authority.**
   Vector proximity to a past query does not prove that a decision is safe.

3. **Neural models never serve production requests directly.**
   `ink-decision-small` operates only in the background compiler. It never intercepts live user queries.

4. **Authority requires independent verification.**
   Only compiled Exact and Linear artifacts that pass statistical qualification receive serving authority.

5. **Ink always fails open.**
   If an artifact is uncertain, novel, or experiencing errors, Ink dispatches the request to the Host model.

---

## Statistical Qualification Contract

To earn serving authority (`ACTIVE`), a candidate artifact must satisfy an error budget configured per DecisionSite.

Ink evaluates candidates against held-out verification data:

$$\text{Wilson Lower Bound}(k, n) \ge 1.0 - \text{Error Budget}$$

Where:
- $n$ is the number of independent evaluation decisions.
- $k$ is the number of verified errors.
- The Wilson 95% lower bound guarantees that performance clears the site threshold with high statistical confidence.

Point accuracy is not enough. If an artifact achieves 100% accuracy on only 10 samples, its Wilson lower bound is approximately 72%. Ink refuses promotion until enough verified evidence accumulates.

---

## Drift Detection & Automatic Demotion

Customer behavior and business policies change over time.

Ink continuously monitors verified outcomes on the active serving path:
- Every outcome updates rolling calibration statistics.
- If verified accuracy drops below the site error budget, Ink immediately revokes serving authority (`ACTIVE` $\to$ `SHADOW`).
- Live traffic returns to the Host model until a new candidate compiles and qualifies.

---

## DecisionSite Fit

### Good DecisionSites
- **Routing & Triage**: Directing customer queries to support categories.
- **Tool Selection**: Choosing among a bounded set of tools in an autonomous agent.
- **Workflow Gates**: Approving, rejecting, or escalating business events.
- **Retry & Recovery**: Selecting retry strategies based on error signatures.

### Poor DecisionSites
- **Open-Ended Writing**: Creative generation, essays, or conversational answers.
- **Unbounded Arguments**: Extracting arbitrary code or open text without discrete choices.
- **Zero-Feedback Tasks**: Workflows where outcomes cannot be verified or measured.
