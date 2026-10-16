# Ink benchmarks

Reproducible benchmark evaluations for the Ink runtime.

The benchmarks measure:
- Fast Path local serving rate
- Serving latency (p50 and p99)
- Statistical safety under distribution drift
- Model call avoidance & token savings

---

## Benchmark suite

The canonical benchmark compares Ink against an exact cache, a semantic cache, a smaller model, and a classical classifier.

### Run

```bash
PYTHONPATH=python/ink python -m benchmarks --workload all --arms all
```

To run a specific workload:

```bash
PYTHONPATH=python/ink python -m benchmarks --workload support_routing
```

Supported workloads:
- `support_routing`: Multi-class customer request dispatch.
- `tool_routing`: Agent tool selection across bounded actions.
- `incident_triage`: Severity classification and on-call routing.

---

## Metrics

The benchmark measures three primary outcomes:

1. **Local Serving Rate:** Percentage of decisions served locally in `< 0.1ms` without calling the model.
2. **Error Rate:** Percentage of local serves that disagree with verified outcomes.
3. **Statistical Confidence:** 95% Wilson score interval on local accuracy against the error budget.

---

## Honest limitations

- Workloads in this directory use synthetic and public datasets for reproducible evaluation.
- Results reflect bounded classification tasks. They do not represent open-ended text generation.
- Production performance depends on site traffic volume, state consistency, and verification latency. If your workload never repeats, Ink saves zero calls.
