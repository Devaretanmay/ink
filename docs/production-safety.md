# Production Safety

Ink focuses on safely accelerating AI decisions by aggressively bounding and verifying behavior.

## Qualification Lifecycle
Every decision site follows a strict lifecycle before entering production serving:
1. **Observe:** Gather real decision traces and outcomes.
2. **Shadow:** Qualify repeated decisions locally. Evaluate whether local fast paths accurately match or out-perform the model.
3. **Active:** Fast paths serve live traffic based on verified success thresholds.
4. **Deopt:** If real-world drift is detected or outcomes fail, the fast path is immediately demoted and control falls back to the model.

## Comparison Traffic
Ink relies on a strict temporal evaluation split (e.g., 70/30) to evaluate whether a compiled fast path performs reliably compared to live model calls.

## Drift Detection Mechanics
Ink continuously monitors live outcomes. In competitive benchmarks under drift, traditional semantic caches produced 12–18% stale wrong-serve rates, while Ink incurred only 7–8 wrong serves before automatically detecting the drift and demoting the fast path.

## "Verified" Definition
"Verified" means that a decision has been evaluated against independent, objective outcome signals from the system (e.g., successful compilation, test pass, user acceptance). High-entropy workloads that cannot be verified are correctly rejected.

## Failure Modes and Mitigations
- **Integrity Failures:** Handled by SHA-256 verification before serving; triggers model fallback.
- **Service Crashes:** Safe fallback to standard application logic (the model handles the decision).
- **Drift:** Continuous outcome tracking automatically deoptimizes degrading paths.

## Health Monitoring & Recommended Practices
- Monitor the ratio of Observed to Active traffic.
- Track verified errors and revocations.
- Regularly review the application's definition of `client.record_outcome()` to ensure outcome fidelity.
