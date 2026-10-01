# Five-Minute Quickstart: Zero-Touch Discovery to Local Fast Path

Get from raw agent execution traces to local verified fast paths in 5 minutes.

---

### Step 1: Install Microloop

```bash
pip install microloop
```

*(Zero neural model download required for discovery, profiling, exact, or sparse semantic paths).*

---

### Step 2: Discover Compilable Sites

Run discovery on your existing agent traces:

```bash
microloop discover examples/five_minute_onboarding/traces.jsonl
```

Output:
```text
Found 2 candidate call sites.

1. support.route
   traffic: 100/day (100 observed)
   repetition: 85.0%
   choices: 3 ['refund', 'request_info', 'specialist']
   verifier readiness: VERIFIER READY (100.0% coverage)
   model latency: 125.0ms
   estimated break-even: 160 decisions
   estimated annual savings: $12.38
   recommendation: STRONG CANDIDATE
   reason: Strong candidate: 85.0% repetition, bounded choices (3), break-even in ~160 decisions.
```

Or view the ready-to-paste integration snippet:

```bash
microloop discover examples/five_minute_onboarding/traces.jsonl --snippet
```

---

### Step 3: Run the Instrumented Example

```bash
python examples/five_minute_onboarding/agent.py
```

The script:
1. Records initial decisions via your original fallback LLM.
2. Compiles and qualifies `support.route` in shadow.
3. Automatically serves repeated queries locally in <0.5ms.

---

### Step 4: Inspect Local Value & ROI

Check your local savings anytime:

```bash
microloop value
```
