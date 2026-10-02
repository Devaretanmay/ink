# Vision: Microloop Future Strategy

## Current State: Fast Paths
Microloop currently implements **Fast Paths**: verifying repeated, single-step decisions and executing them locally. Based on benchmarks, bounded and verifiable decision traffic constitutes approximately 15–25% of total app LLM calls, yielding a whole-app call reduction of 3.99–10.10% and whole-app spend reduction of 3.51–8.87%, with local execution at ~0.18–0.19 ms.

## Future Concept: Long Paths
**Long Paths** represent verified, repeated multi-step agent behavior compiled into local procedures, including safe checkpoints and deoptimization loops. 

*Note: Long Paths are NOT currently implemented.*

### Criteria to Build Long Paths
We will only pursue Long Paths if and when:
1. At least 3 independent design partners demonstrate that individual DecisionSites (Fast Paths) are useful.
2. It becomes evident that the vast majority of remaining latency and cost bottlenecks lie in repeated multi-step agent trajectories.
