# Value Model

How to evaluate Ink's impact on production AI workloads.

---

## The Core Value Proposition

Ink is not simply a tool for reducing token spend. Token savings are often the least important benefit.

A complete evaluation should account for:

| Value Driver | Mechanism | Typical Impact |
| :--- | :--- | :--- |
| **Avoided remote model calls** | Decisions served locally bypass network round-trips to the LLM provider | 20–40% fewer calls inside qualified bounded decision sites |
| **Latency reduction** | Exact Fast Path serves execute in 0.18–0.19 ms (p50) vs. 120–250 ms for remote API calls; learned local decisions run at ~48 ms | Exact Fast Path serves are approximately 700x faster than a 130 ms remote call; learned decisions are roughly 3x faster |
| **Rate-limit headroom** | Avoided calls reduce pressure on provider TPM/RPM quotas | Fewer 429 errors during traffic spikes |
| **Provider resilience** | Qualified decisions serve locally even if the remote provider is degraded | Partial outage tolerance for critical paths |
| **Privacy / locality** | Decision state for qualified paths never leaves the process boundary | Zero network transmission during local serving |
| **Stale-decision revocation** | Continuous comparison traffic detects policy drift and demotes automatically | Avoids the silent failure modes of static caches |

---

## Expected Value Equation

```text
Expected Value = (Covered Decisions × Local Serve Rate × Avoided Cost/Latency)
                 − (Integration Cost + Verification Cost)
```

Where:
- **Covered Decisions**: Total volume of decisions that fall within bounded, repetitive sites.
- **Local Serve Rate**: Percentage of decisions served from Fast Paths (typically 20–40% of site traffic after qualification).
- **Avoided Cost/Latency**: Direct API cost and execution time saved per avoided call.
- **Integration Cost**: One-time engineering effort to wrap decision sites and wire outcome verifiers.
- **Verification Cost**: Comparison traffic overhead (5–35% during active serving to monitor for drift) and initial shadow qualification calls.

---

## When Ink Is NOT Justified

Be honest: not all workloads benefit from Ink.

```text
Do NOT adopt Ink if:
├── Your decisions are open-ended text generation (summaries, creative writing, chat)
├── Repetition rate is < 20% (mostly unique states with no recurring patterns)
├── You cannot independently verify decision correctness (no downstream signal)
├── Decision volume is low (< 500 decisions/month per site)
└── Remote model latency is already acceptable and API costs are trivial
```

### High-Entropy Workloads

In our benchmarks, high-entropy workloads (e.g., open-ended research tasks with unique state IDs) were correctly rejected by Ink's profiler with **0% wasted qualification resources**. Attempting to cache or fast-path these workloads produces negative ROI because the qualification cost is never amortized.

---

## DecisionSite vs. Whole-Application Impact

A common evaluation mistake is extrapolating DecisionSite-level numbers to the entire application.

```text
Application LLM Calls (100%)
├── Open-ended generation (60–75%) ──► Continues to remote LLM (not bounded)
├── Novel/complex reasoning (10–15%) ──► Continues to remote LLM (novelty)
└── Bounded decisions (15–25%)      ──► Handled by Ink DecisionSites
    ├── Qualified Fast Path (20–40%) ──► Served locally in < 0.2 ms
    ├── Comparison traffic (5–35%)   ──► Sent to LLM for drift monitoring
    └── Fallback to LLM (remaining)  ──► Unqualified states continue to LLM
```

In tested benchmark workloads:
- **DecisionSite call reduction:** 19.9–40.4% within bounded decision sites
- **Whole-application call reduction:** 3.99–10.10% across all application calls
- **Whole-application spend reduction:** 3.51–8.87% across total application spend

Evaluate your workload's bounded decision share using `ink discover` before making architectural commitments.
