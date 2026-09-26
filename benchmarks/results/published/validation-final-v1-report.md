# Microloop Evidence Audit: validation-final-v1

```
==============================================================================
        MICROLOOP BENCHMARK AUDIT REPORT: VALIDATION-FINAL-V1
==============================================================================
Manifest Split : validation-final (100 paired tasks evaluated)
Exact Model ID : gpt-6-astra (openai)
Harness Commit : f6a91c828d54 | mini-swe-agent v2.4.6
Evaluator Comm : d4e1f728c70a
Docker Digest  : sha256:4a38f3281b9b...
------------------------------------------------------------------------------
Metric                    | Vanilla         | Microloop       | Difference     
------------------------------------------------------------------------------
Tasks solved              | 51/100          | 63/100          | +12            
ACR                       |          51.0% |          63.0% |        +12.0 pp
95% paired CI             | —               | —               | [+5.0, +20.0] pp
McNemar test p-value      | —               | —               | p = 0.0042
Median tool calls         |              61 |              48 | -21.3%
Wilcoxon tool-call p      | —               | —               | p = 0.00000
Mean token cost / task    | $        0.9143 | $        0.7128 | -22.0%
Damaging interventions    | —               |            2/51 | 3.9%
Successful recoveries     | —               |           19/27 | 70.4%
==============================================================================
```
