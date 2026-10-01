# Integrated model validation — 2026-09-29

Microloop Decision v1 is now integral: the package contains the neural
architecture, tokenizer adapter, inference runtime, pinned checkpoint manifest,
and atomic model installer. Default compilation uses it. The product CLI has no
engine selector. Source and weights keep their upstream provenance.
Weight values were not changed.

A clean rebuilt-wheel environment with **no `laya-mlx` installed** passed all
**157 Python tests**, with zero skipped tests. Real-weight tests cover default
compilation, qualification, persisted restart, local serving, unseen-state
fallback and engine failure. Model setup tests reject tampering and clean up
interrupted copies. The wheel contains the source, model manifest and notices.
Lint, whitespace checks and workflow YAML parsing pass. Rust code is unchanged;
its previously recorded checks are historical, not claimed as rerun here.

The generated refund operational ledger completed the full lifecycle using the
bundled neural engine and a labelled deterministic original fallback:

- 3,000 observe decisions and 600 shadow decisions.
- 405 of 1,800 steady-state decisions served locally; outcome quality 1.0.
- All six novel cases fell back.
- Deliberate drift demoted the candidate to SHADOW; all three subsequent
  decisions used fallback and passed the changed ledger policy.
- Database integrity and foreign keys passed after the run.

This run proves neural integration and lifecycle behavior, not cloud-model cost
savings. Dependency/platform fingerprint binding was added after the full demo;
real lifecycle tests were then repeated against the final rebuilt wheel.
Artifacts now require requalification after runtime dependency or platform
changes. Historical external-runtime evidence remains separately identified.

CPU float32 inference also ran successfully on this Mac. Linux x86_64 dependency
resolution succeeded for glibc 2.35+. Linux execution is still unverified. CI and
release workflows now target macOS/Linux and provision the real model before
neural integration tests. No CI run or package publication was triggered.

The public BANKING77 probe scored 120/120 on its untouched three-intent test
subset. No confidence threshold met the predeclared statistical qualification
bound. Production promotion stays off; public label agreement cannot replace
customer outcomes or establish exact-state fast-path coverage. See the
[reproduction and calibration plan](../benchmarks/public_support/README.md).

Saved evidence:

- [Wheel and lifecycle results](evidence/integrated-model.json)
- [Final runtime benchmark](evidence/integrated-benchmark.json)
- [CPU smoke](evidence/integrated-cpu.json)
- [Public benchmark and frozen thresholds](evidence/public-banking77-integrated.json)

v0.4 remains unreleased. Cloud-provider E2E, actual customer outcomes, production
qualification and Linux execution are still separate validation gates.
