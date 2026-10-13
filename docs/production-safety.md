# Production Safety

Ink enforces explicit statistical safety contracts before allowing any local software artifact to serve production traffic.

Serving decisions locally without calling a remote model introduces operational risk if the boundary is inaccurate. Ink's entire architecture is built to eliminate premature serving authority and continuously guard against drift.

---

## 1. What Does NOT Grant Serving Authority

In many AI systems, local models or routers are deployed based on unverified heuristics. In Ink, the following properties have **zero authority**:

1. **Model Confidence Does Not Grant Authority.**
   A model predicting 99.9% softmax confidence on an input is not evidence of correctness. Overconfident models frequently make high-confidence mistakes on out-of-distribution inputs.
2. **Vector Similarity Does Not Grant Authority.**
   High cosine similarity to a historical embedding vector does not prove that a decision remains correct under current business rules.
3. **Candidate Model Identity Does Not Grant Authority.**
   Using a modern neural representation (such as `ink-decision-small`) does not entitle an artifact to serve live traffic. The model's identity is an implementation detail, not proof of safety.
4. **Point Accuracy on Small Samples Does Not Grant Authority.**
   An artifact that scores 10/10 correct on a test set has not proven safety. Small sample sizes have broad uncertainty intervals.

---

## 2. What DOES Grant Serving Authority

Serving authority is strictly evidence-based and evaluates multiple statistical requirements across independent partitions.

### A. Generic Promotion Requirements (`PromotionRequirements`)
Ink's qualification policy evaluates holdout and fresh shadow validation traffic across several explicit gates:
- **Sample Thresholds**: Candidate artifacts must accumulate at least `min_samples` on holdout and shadow evaluation tasks, and `min_region_samples` within each candidate region.
- **Finite-Sample Quality Bound**: Evaluates a finite-sample lower bound $q_{\text{lower}} = \hat{q} - \sqrt{\frac{\ln(20)}{2n}}$ (clearing the Hoeffding $95\%$ confidence lower bound against `min_quality`).
- **Host Comparison Degradation Gate**: Evaluates comparison trials against the Host model to ensure local candidates do not degrade past `max_degradation` ($\delta_{\text{lower}} \ge -\text{max\_degradation}$).
- **Verifier Provenance Invariant**: The verifier identity (`verifier`, `verifier_version`) used during evaluation must match calibration evidence exactly.
- **Evidence Identity Digest**: Checksumming ensures stale model checkpoints or modified fallback revisions cannot inherit authority.

### B. Site-Level Error Budget Contract (`local_error_budget`)
When a DecisionSite specifies an explicit `local_error_budget` (e.g., $\le 1.0\%$ or $\le 5.0\%$), Ink enforces statistical safety via the Wilson score interval:

$$\text{Wilson Lower Bound}(k, n) \ge 1.0 - \text{local\_error\_budget}$$

Where:
- $n$ is the count of independent evaluation observations.
- $k$ is the count of verified errors.
- $\text{local\_error\_budget}$ is the site's maximum tolerated error rate.

Both the empirical point accuracy and the 95% Wilson confidence lower bound must satisfy the contract before promotion to `ACTIVE`.

### Statistical Rigor Over Point Estimates
A standard point accuracy estimate $\hat{p} = (n - k) / n$ ignores statistical uncertainty. If $n = 10$ and $k = 0$, point accuracy is $100\%$, but the 95% Wilson confidence lower bound is only $72.2\%$.

If a DecisionSite requires an error budget of $\le 1.0\%$ (requiring $99\%$ accuracy), Ink requires hundreds of verified evaluation samples with near-zero errors before the Wilson lower bound clears $0.99$.

Until all qualification thresholds are met, the DecisionSite remains in `OBSERVE` or `SHADOW` mode. Traffic continues to route to the Host model.

---

## 3. Local Coverage vs. Raw Accuracy

In traditional machine learning, models are evaluated on global accuracy over an entire dataset.

In Ink, **coverage** and **accuracy** are decoupled:
- **Coverage**: The proportion of incoming traffic that falls into high-density, proven decision regions.
- **Accuracy**: The verified precision within those specific covered regions.

Ink will operate at **40% coverage** if it can prove that those 40% of requests satisfy a 99% accuracy requirement. The remaining 60% of requests are novel, ambiguous, or rare, and Ink routes them safely to the Host model.

A Fast Path does not need to solve the entire problem space to deliver value. It resolves the proven core and leaves novelty to the Host model.

---

## 4. Sites Can Remain SHADOW or Be Retained in OBSERVE

Ink does not force compilation.

If an audit or evaluation reveals that:
- Evidence volume is insufficient to satisfy the statistical bounds
- The distribution of inputs is non-stationary and unpredictable
- The candidate artifact exceeds the site error budget on holdout trials

Ink retains the candidate in `SHADOW` or keeps the site in `OBSERVE`. Local execution is not granted, and the application continues using the Host model.

A negative result is a valid and safe outcome. Ink protects production applications by refusing to guess.

---

## 5. Qualification Is Not Permanent Correctness

Passing qualification is not an eternal guarantee. Real-world systems drift:
- API payloads change format
- User intent evolves with seasonal trends
- Downstream business rules and legal policies update

Ink maintains continuous verification on the active serving path:
- **Comparison Traffic**: A randomized proportion of active traffic (e.g., 5% to 25%) continues to execute the Host model concurrently.
- **Rolling Drift Accounting**: Verified outcomes from comparison traffic are logged to the evidence ledger.
- **Drift Revocation Lifecycle**:
  1. Comparison decisions and real outcomes are durably recorded in SQLite.
  2. Drift evaluation is triggered—either via `ink.reevaluate()`, scheduled maintenance (`ink.maintenance()`), or background maintenance if `auto_maintenance=True`.
  3. If verified error rates exceed the budget or comparison degradation is detected, Ink atomically demotes the artifact (`ACTIVE` $\to$ `SHADOW`).
  4. Subsequent requests route to the Host model without human intervention until fresh evidence qualifies.

*(Note: When `auto_maintenance=False`, drift reevaluation occurs whenever `maintenance()` or `reevaluate()` is invoked by the application or operational jobs).*

---

## 6. Verifier Quality & Provenance

Statistical rigor is only as trustworthy as the ground-truth outcomes fed into the system.

A mathematically defensible Wilson confidence bound calculated against flawed or hallucinated verifier feedback is still bad evidence.

Ink requires teams to inspect outcome provenance:
- **High-Integrity Signals**: Deterministic test passes, successful downstream database transactions, completed customer checkouts, human reviewer approvals.
- **Low-Integrity Signals**: Model-as-a-judge scores, speculative regex heuristics, self-reported agent claims.

When integrating a DecisionSite, always pair it with the highest-integrity outcome signal available in your application pipeline.

---

## 7. Absolute Safety vs. Empirical Rigor

In production software engineering, no machine learning system can offer "absolute mathematical certainty" or "zero risk."

Ink does not claim absolute immunity to error. What Ink provides is **transparent, verifiable statistical governance**:
- Explicit error budgets that you configure.
- Proven confidence bounds calculated over independent data.
- Controlled local coverage that abstains on novelty.
- Fail-open fallback to your frontier Host model.
- Continuous drift revocation when outcomes shift.
