# Public support calibration probe

This uses the established [PolyAI BANKING77 dataset](https://github.com/PolyAI-LDN/task-specific-datasets),
licensed CC-BY-4.0, pinned at commit
`57ec275d8078af65b7731c2a98be812d844a6d6b`. Attribution: PolyAI and the BANKING77
creators. It is a public intent-classification benchmark, not Ink customer
traffic, a production refund ledger, or independently observed business outcomes.

```bash
ink model-install
python benchmarks/public_support/evaluate.py --output .ink/public-support-new
```

The probe selects three declared intents before evaluation. Fixed instructions
configure the bundled model; no weights are trained. The official train split
calibrates six predeclared confidence thresholds. A Bonferroni correction
accounts for selecting among them. The profile is saved before official test
queries are processed. Duplicate normalized texts are excluded across splits.
Customer/task identity is unavailable, so related conversations cannot be
reliably grouped. Prior exposure of the pretrained weights to this public
benchmark is unknown. Do not claim uncontaminated generalization.

Saved outputs include source files/license/hashes, frozen profile, predictions
identified by text hashes, and aggregate results. No provider credential or
customer data is required. This measures intent-label agreement only, with no
paired fallback quality or model-call-savings claim.

The local run observed 493/497 correct calibration labels and 120/120 correct
held-out labels. No threshold passed the declared 95% quality lower bound at
95% confidence after selection correction. All automatic promotion stays off.
Unique held-out strings also have no exact-state training overlap, so the
current conservative dispatcher cannot qualify these as repeated fast paths.
A high classifier score does not establish serving coverage.

## Production calibration path

Keep the provisional target explicit: at least 95% independently verified
quality, at most one percentage point below the original fallback, 95%
confidence. These are experiment assumptions, not a universal business SLA.

Collect task-linked, representative repeated states and independently replayable
outcomes. Freeze schemas, model weights, verifier, candidate regions and risk
tolerance. Choose thresholds on calibration tasks, then evaluate untouched
later tasks with paired original-fallback and candidate outcomes. Missing
outcomes, insufficient regional support, or failed non-inferiority keep shadow
mode. Only after qualification should randomized fallback comparisons monitor
active service and demote drifted regions.

With the current conservative Hoeffding bounds, even perfect quality needs
600 independent samples for a single predeclared 95% lower bound. Searching six
thresholds needs 958 perfect calibration samples. A one-point paired degradation
bound at zero observed difference needs 59,915 independent pairs. These are
bound-specific planning figures; do not manufacture independence by replaying
the same task under new IDs. Choose a better justified statistical method or
collect the required evidence before calling thresholds production-calibrated.
