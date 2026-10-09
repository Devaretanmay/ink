# Design Partner Production Pilot Checklist

This checklist guides engineering teams deploying Ink in production pilot environments.

---

## Phase 1: Site Selection (Day 1)

- [ ] **Identify Target Node:** Choose 1–3 bounded decision nodes handling $> 500$ calls/day.
  - Good candidates: `triage.ticket_route`, `agent.tool_choice`, `approval.fraud_check`, `workflow.escalate_step`.
- [ ] **Declare State Schema:** Define primitive fields (`string`, `number`, `boolean`, `integer`). Strip nonces and random IDs.
- [ ] **Enumerate Choices:** Ensure all possible branches at this node are declared as discrete strings.
- [ ] **Identify Ground Truth Verifier:** Determine how success is confirmed (e.g. error status, user acceptance, downstream task completion).

---

## Phase 2: Integration & Observation (Days 2–3)

- [ ] **Wrap Decision Function:** Integrate using `@ink.wrap(site)` or `ink.decide(...)`.
- [ ] **Record Outcomes Asynchronously:** Ingest task resolution outcomes via `ink.record_outcome()`.
- [ ] **Verify Fail-Open Semantics:** Ensure host fallback executes normally without interruption.
- [ ] **Run Discovery & Doctor Checks:**
  ```bash
  ink doctor
  ink status
  ```

---

## Phase 3: Shadow Calibration (Days 4–5)

- [ ] **Compile Candidate Fast Path:**
  ```bash
  ink compile <site_name> --engine auto
  ```
- [ ] **Calibrate Candidate:**
  ```bash
  ink evaluate <site_name> --verifier <module:func>
  ```
- [ ] **Monitor Shadow Progress:** Use `ink console` to observe shadow decision agreement and progressive region accumulation.

---

## Phase 4: Production Fast Path Serving (Days 6–7)

- [ ] **Verify ACTIVE Promotion:** Confirm the site has achieved statistical qualification.
- [ ] **Measure Latency & Cost Impact:**
  - Fast Path latency: $< 1\text{ ms}$
  - Model API calls: Reduced by $40\%\text{--}80\%$
- [ ] **Generate Pilot Evaluation Report:**
  ```bash
  python examples/pilot_report.py --db .ink/decisions.db --output pilot_report.md
  ```
- [ ] **Review Drift Safeguards:** Verify comparison traffic sampling is active and demotion alerts are monitored.
