# Microloop Decision — model card

Identity: `microloop-decision` (runtime `microloop-decision-v1.0`).
Status: v1 ships pinned base weights; owned weights land via `training.finetune`.

## Lineage

- v1 base: pinned upstream checkpoint (hashes in `checkpoint.json`,
  provenance in `NOTICE` and `upstream.json`). `weights_modified: false`.
  Not claimed as Microloop-trained.
- Owned candidates: directories written by
  `microloop.internal.model.training.finetune` carry `microloop-model.json`
  with `weights_modified: true`, `trained_by: microloop-finetune-v1`,
  trainable scope (`head.*`, `scorer.*`), base manifest, dataset digest,
  steps/lr/seed, and held-out before/after accuracy. The encoder stays frozen
  in v1 fine-tunes; that is recorded in the card, not assumed.

## Ownership rule

Only a checkpoint with `weights_modified: true` plus a training lineage card
may be called Microloop-trained. Training accuracy is a smoke signal, never
qualification: promotion still requires compile → calibrate → evaluate →
shadow on independent outcome data with frozen verifier identity.

## Platform

Neural inference: Linux (x86_64, glibc 2.35+, CPU) and Apple Silicon macOS.
Windows raises a clear platform error; use `engine="exact"` there.
Default CPU precision is float16; training uses float32.
