# Ink Sales Demo Recording Script (~75 Seconds)

**Target Duration:** 75 seconds  
**Format:** Screen recording with voiceover  
**URL:** Open `demo/index.html` (or run `python3 -m http.server 8000` from `demo/` and open `http://localhost:8000`)

---

## Visual & Voiceover Breakdown

### Act 1: The Status Quo (0:00 – 0:10)
- **Visual:** Focus on the **Live Decision Stream**. Normal tickets arriving (`#1831`, `#1832`, `#1833`). Every ticket invokes `MODEL` in gray badge. Latency displays `148 ms`, `151 ms`, `143 ms`.
- **Voiceover:**
  > *"This customer support agent is calling a frontier model to route every single incoming ticket — even when the exact same decisions repeat thousands of times every day at 150 milliseconds per call."*

---

### Act 2: Qualification & Fast Path (0:10 – 0:25)
- **Visual:** Status indicator transitions from **OBSERVE** to **SHADOW** then **ACTIVE**. Ticket `#1836` arrives: *"Item shattered in transit, requesting full refund."* Badge flashes green: `FAST PATH`. Latency drops from `148 ms` to `0.18 ms`. Progress bar fills with green.
- **Voiceover:**
  > *"Ink observes those decisions and tracks their downstream verified outcomes. Once a repeated decision earns enough statistical evidence, it compiles into a local Fast Path.*
  > 
  > *Decision latency drops from 148 milliseconds to 0.18 milliseconds — over 800 times faster, executing entirely in-process without an API call."*

---

### Act 3: Policy Shift & Automatic Deopt (0:25 – 0:50)
- **Visual:** Click **"⚡ Inject Policy Change"** (or auto-triggers). Red banner appears: *"POLICY UPDATE: Damaged electronics can no longer be automatically refunded. They require specialist review."*
  - Look at **Semantic Cache**: Serves cached `refund` ❌ (stale corrupt serves, error count climbs to 270 / 18.0%).
  - Look at **Ink**: Active comparison traffic detects disagreement on damaged tickets. After 7–8 disagreements, status immediately transitions:
    `ACTIVE` → `SHADOW (DEOPT)` → `MODEL FALLBACK`.
  - Next ticket routes safely back to `MODEL` → `specialist` ✓ in 148 ms.
- **Voiceover:**
  > *"Now here is what separates Ink from an AI cache: what happens when reality changes.*
  > 
  > *Suppose your business policy updates: damaged electronics now require specialist review instead of automatic refunds.*
  > 
  > *A semantic cache blindly continues serving stale refunds at an 18% error rate.*
  > 
  > *Ink actively compares incoming traffic against real outcomes. It detects the drift, deoptimizes the fast path back to shadow, and steps down to the model — stopping stale decisions in under eight calls."*

---

### Act 4: The Empirical Safety × Savings Frontier (0:50 – 0:65)
- **Visual:** Scroll to the **Empirical Safety × Savings Frontier** chart. Hover over the green point `Ink (Demotes in 8 calls)` at (3.99% whole-app savings, 2.68% error rate) compared to high-error semantic cache points (18% error) and expensive model baselines. Toggle between "Whole-Application %" and "DecisionSite Only %".
- **Voiceover:**
  > *"In our 18,000-decision competitive benchmark across four workloads, this creates a strictly superior safety-versus-savings frontier.*
  > 
  > *At equivalent model-call reduction, Ink produces substantially fewer incorrect decisions than static caches or uncalibrated classifiers."*

---

### Act 5: Autonomous Refusal & Closing (0:65 – 0:75)
- **Visual:** Scroll to the **Negative Control (Research Agent)** card. Highlight the red box: `RECOMMENDATION: DO NOT COMPILE (Reason: high entropy)`. Show `Local calls served: 0, Wrong local serves: 0`.
- **Voiceover:**
  > *"And when a workload isn't repetitive — like an exploratory research agent — Ink's profiler refuses to compile it. Zero wasted qualification calls.*
  > 
  > *Models handle novelty. Ink handles verified repetition.*
  > 
  > *If your agents repeat themselves, send us a sanitized trace sample, and we'll show you what's compilable before you touch a line of production code."*

---

## Technical Notes for Presenter / Recorder
- Ensure the demo runs at native resolution (1920x1080 or 1440p).
- The automatic timer is calibrated to advance steps every 1.8 seconds.
- You can pause at any time using the `⏸ Pause` button to speak to a specific customer point.
