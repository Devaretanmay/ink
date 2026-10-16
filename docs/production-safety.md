# Production safety

Ink enforces explicit statistical safety contracts before allowing any local software artifact to serve production traffic.

Serving decisions locally without calling a remote model introduces operational risk if the boundary is inaccurate. Ink's entire architecture is built to eliminate premature serving authority and continuously guard against drift.

---

## 1. What does NOT grant serving authority

In many AI systems, local models or routers are deployed based on unverified heuristics. In Ink, the following properties have **zero authority**:

1. **Model confidence does not grant authority.**
   A model predicting 99.9% softmax confidence on an input is not evidence of correctness. Overconfident models frequently make high-confidence mistakes on out-of-distribution inputs.
2. **Vector similarity does not grant authority.**
   High cosine similarity to a historical embedding vector does not prove that a decision remains correct under current business rules.
3. **Candidate model identity does not grant authority.**
   Using a modern neural representation (such as `ink-decision-small`) does not entitle an artifact to serve live traffic. The model's identity is an implementation detail, not proof of safety.
4. **Point accuracy on small samples does not grant authority.**
   An artifact that scores 10/10 correct on a test set has not proven safety. Small sample sizes have massive uncertainty intervals.

---

## 2. What DOES grant serving authority

Serving authority is strictly evidence-based and evaluates multiple statistical requirements across independent partitions.

### A. Generic promotion requirements (`PromotionRequirements`)
Ink's qualification policy evaluates holdout and fresh shadow validation traffic across several explicit gates:
- **Sample thresholds:** Candidate artifacts must accumulate at least `min_samples` on holdout and shadow evaluation tasks, and `min_region_samples` within each candidate region.
- **Finite-sample quality bound:** Evaluates a finite-sample lower bound $q_{\text{lower}} = \hat{q} - \sqrt{\frac{\ln(20)}{2n}}$ (clearing the Hoeffding $95\%$ confidence lower bound against `min_quality`).
- **Host comparison degradation gate:** Evaluates comparison trials against the Host model to ensure local candidates do not degrade past `max_degradation` ($\delta_{\text{lower}} \ge -\text{max\_degradation}$).
- **Verifier provenance invariant:** The verifier identity (`verifier`, `verifier_version`) used during evaluation must match calibration evidence exactly.
- **Evidence identity digest:** Checksumming ensures stale model checkpoints or modified fallback revisions cannot inherit authority.

### B. Site-level error budget contract (`local_error_budget`)
When a DecisionSite specifies an explicit `local_error_budget` (e.g., $\le 1.0\%$ or $\le 5.0\%$), Ink enforces statistical safety via the Wilson score interval:

$$\text{Wilson Lower Bound}(k, n) \ge 1.0 - \text{local\_error\_budget}$$

Where:
- $n$ is the count of independent evaluation observations.
- $k$ is the count of verified errors.
- $\text{local\_error\_budget}$ is the site's maximum tolerated error rate.

Both the empirical point accuracy and the 95% Wilson confidence lower bound must satisfy the contract before promotion to `ACTIVE`.

### Statistical rigor over point estimates
A standard point accuracy estimate $\hat{p} = (n - k) / n$ ignores statistical uncertainty. If $n = 10$ and $k = 0$, point accuracy is $100\%$, but the 95% Wilson confidence lower bound is only $72.2\%$.

If a DecisionSite requires an error budget of $\le 1.0\%$ (requiring $99\%$ accuracy), Ink requires hundreds of verified evaluation samples with near-zero errors before the Wilson lower bound clears $0.99$.

Until all qualification thresholds are met, the DecisionSite remains in `OBSERVE` or `SHADOW` mode. Traffic continues to route to the Host model.

---

## 3. Local coverage vs. raw accuracy

In traditional machine learning, models are evaluated on global accuracy over an entire dataset.

In Ink, **coverage** and **accuracy** are decoupled:
- **Coverage:** The proportion of incoming traffic that falls into high-density, proven decision regions.
- **Accuracy:** The verified precision within those specific covered regions.

Ink will happily operate at **40% coverage** if it can prove that those 40% of requests satisfy a 99% accuracy requirement. The remaining 60% of requests are novel, ambiguous, or rare, and Ink routes them safely to your model.

A Fast Path does not need to solve the entire problem space to deliver value. It resolves the proven core and leaves novelty to your model.

---

## 4. Sites can remain SHADOW or stay in OBSERVE

Ink does not force compilation.

If an audit or evaluation reveals that:
- Evidence volume is insufficient to satisfy the statistical bounds
- The distribution of inputs is non-stationary and unpredictable
- The candidate artifact exceeds the site error budget on holdout trials

Ink keeps the candidate in `SHADOW` or keeps the site in `OBSERVE`. Local execution is not granted, and the application continues using your model.

A negative result is a safe outcome. Ink protects production applications by refusing to guess.

---

## 5. Qualification is not permanent correctness

Passing qualification is not an eternal guarantee. Real-world systems drift:
- API payloads change format
- User intent evolves with seasonal trends
- Downstream business rules and legal policies update

Ink maintains continuous verification on the active serving path:
- **Comparison traffic:** A randomized proportion of active traffic (e.g., 5% to 25%) continues to execute your Host model concurrently.
- **Rolling drift accounting:** Verified outcomes from comparison traffic are logged to the evidence ledger.
- **Drift revocation lifecycle:**
  1. Comparison decisions and real outcomes are durably recorded in SQLite.
  2. Drift evaluation is triggered—either via `ink.reevaluate()`, scheduled maintenance (`ink.maintenance()`), or background maintenance if `auto_maintenance=True`.
  3. If verified error rates exceed the budget or comparison degradation is detected, Ink atomically demotes the artifact (`ACTIVE` $\to$ `SHADOW`).
  4. Subsequent requests route to your Host model without human intervention until fresh evidence qualifies.

*(Note: When `auto_maintenance=False`, drift reevaluation occurs whenever `maintenance()` or `reevaluate()` is invoked by the application or operational jobs).*

---

## 6. Verifier quality & provenance

Statistical rigor is only as trustworthy as the ground-truth outcomes fed into the system.

A mathematically defensible Wilson confidence bound calculated against flawed or hallucinated verifier feedback is still bad evidence.

Ink requires teams to inspect outcome provenance:
- **High-integrity signals:** Deterministic test passes, successful downstream database transactions, completed customer checkouts, human reviewer approvals.
- **Low-integrity signals:** Model-as-a-judge scores, speculative regex heuristics, self-reported agent claims.

When integrating a DecisionSite, always pair it with the highest-integrity outcome signal available in your application pipeline.

---

## 7. Absolute safety vs. empirical rigor

In production software engineering, no machine learning system can offer "absolute mathematical certainty" or "zero risk."

Ink does not claim absolute immunity to error. What Ink provides is **transparent, verifiable statistical governance**:
- Explicit error budgets that you configure.
- Proven confidence bounds calculated over independent data.
- Controlled local coverage that abstains on novelty.
- Fail-open fallback to your frontier Host model.
- Continuous drift revocation when outcomes shift.
