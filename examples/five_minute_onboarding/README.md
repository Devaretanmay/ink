# Five-minute onboarding: discovery → qualification → local Fast Path

Go from raw agent traces to locally served, verified decisions without a model download.

---

### 1. Install

```bash
pip install ink-jit
```

Discovery, profiling, and the exact engine require no neural model download.

### 2. Discover compilable sites

```bash
ink discover examples/five_minute_onboarding/traces.jsonl
```

```text
Found 2 candidate call sites.

1. support.route
   traffic: 2,000/day (20 observed)
   repetition: 85.0%
   choices: 3 ['refund', 'request_info', 'specialist']
   verifier readiness: VERIFIER_READY (100.0% coverage)
   model latency: 126.0ms
   recommendation: STRONG CANDIDATE
   reason: Strong candidate: 85.0% repetition, bounded choices (3), break-even in ~186 decisions.

2. agent.free_text
   ...recommendation: INVESTIGATE
```

Add `--snippet` for ready-to-paste integration code, or `--profile` for economic detail.

### 3. Run the instrumented example

```bash
python examples/five_minute_onboarding/agent.py
```

The script:

1. Registers the `support.route` DecisionSite.
2. Records baseline decisions through your original model.
3. Compiles and qualifies the site in shadow against independent outcomes.
4. Serves repeated states locally from the active Fast Path.

### 4. Inspect local value

```bash
ink value --db .ink/onboarding_demo/decisions.db
```

See [docs/discovery.md](../../docs/discovery.md) for the trace format.
