# Ink Benchmarks

This directory contains reproducible evaluations of the Ink runtime.

The benchmarks measure:
- Fast Path local serving rate
- Serving latency (p50 and p99)
- Statistical safety under drift
- Host call avoidance

---

## Benchmark Suite

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

1. **Local Serving Rate**: Percentage of decisions served locally without calling the Host.
2. **Error Rate**: Percentage of local serves that disagree with verified outcomes.
3. **Statistical Confidence**: 95% Wilson score interval on local accuracy.

---

## Limitations

- Workloads in this directory use synthetic and public datasets for reproducible evaluation.
- Results reflect bounded classification tasks. They do not represent open-ended text generation.
- Production performance depends on site traffic volume, state consistency, and verification latency.
