# Microloop Evidence Audit: validation-pilot-v1

```
==============================================================================
        MICROLOOP BENCHMARK AUDIT REPORT: VALIDATION-PILOT-V1
==============================================================================
Manifest Split : validation-pilot (20 paired tasks evaluated)
Exact Model ID : gpt-6-astra (openai)
Harness Commit : f6a91c828d54 | mini-swe-agent v2.4.6
Evaluator Comm : d4e1f728c70a
Docker Digest  : sha256:4a38f3281b9b...
------------------------------------------------------------------------------
Metric                    | Vanilla         | Microloop       | Difference     
------------------------------------------------------------------------------
Tasks solved              | 10/20           | 14/20           | +4             
ACR                       |          50.0% |          70.0% |        +20.0 pp
95% paired CI             | —               | —               | [+5.0, +40.0] pp
McNemar test p-value      | —               | —               | p = 0.1250
Median tool calls         |              70 |              56 | -20.6%
Wilcoxon tool-call p      | —               | —               | p = 0.00009
Mean token cost / task    | $        0.9158 | $        0.7136 | -22.1%
Damaging interventions    | —               |            0/10 | 0.0%
Successful recoveries     | —               |             4/4 | 100.0%
==============================================================================
```
