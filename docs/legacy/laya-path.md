# Microloop Decision v1

Microloop now ships its own maintained inference implementation, derived from
laya-mlx 0.2.0 and Laya. The neural model is the default compiler and dispatcher
engine, not an optional plugin. There is no runtime import of `laya_mlx`.

## What is integrated

The private `microloop.internal.model` package contains ModernBERT, decision
heads, tokenization, prompt construction, batching, inference, and checkpoint
provisioning. Microloop owns the modifications and integration; upstream code
and pretrained weights keep their attribution. The canonical engine key is now
`decision`; `laya` (and `microloop-decision-v1`) remain as compatibility aliases
for saved records and internal experiments. Old artifacts carry a different
runtime version and must be recompiled and requalified.

`microloop model-install` downloads one pinned checkpoint revision, validates
all required files against bundled SHA-256 hashes, and atomically installs it
under `~/.cache/microloop/models/decision-v1`. `--checkpoint PATH` provisions
from an existing local checkpoint. `MICROLOOP_MODEL_DIR` sets the installation
location. No network download happens on a decision request. Missing, corrupt,
or incompatible models retain fallback authority.

Runtime dependencies are mandatory: MLX, NumPy, tokenizers, and the explicit
setup downloader. The separate `laya-mlx` distribution is not required.
Supported targets are Python 3.11–3.13, Apple Silicon macOS 14+, and Linux
x86_64 with glibc 2.35+ using MLX CPU. Windows and Intel Mac are outside this
release's supported runtime targets. Linux execution still needs CI evidence;
macOS results must not be presented as Linux validation.

## Weights and qualification

Model identity: `microloop-decision-v1`. Base weights:
`aac6fef/laya-mlx@20aed815fc6acde75733882e7ec0e3f28aeb9717`, derived from
`convaiinnovations/laya`. Base weight values are unchanged and
`weights_modified` is false; the name identifies the Microloop distribution,
not independent pretraining.

## Ownership roadmap (in progress)

1. Surface rebrand (done): canonical engine key `decision`, no engine flag on
   the CLI, upstream naming confined to `NOTICE`, `upstream.json`, vendored
   headers, and historical evidence. `laya` remains only as a stored-artifact
   alias.
2. Owned weights: `microloop.internal.model.training.finetune` (and
   `microloop model-train --data rows.jsonl --output DIR`) fine-tunes the
   decision head + scorer on labeled Microloop rows with a held-out split and
   writes a new checkpoint dir with `microloop-model.json`
   (`weights_modified: true`, base manifest, dataset digest, steps/lr/seed,
   held before/after). Only such checkpoints may be called Microloop-trained.
3. Divergence: encoder stays frozen in v1 fine-tunes (recorded in the card);
   future revisions unfreeze more scope under new runtime versions. See
   `microloop/internal/model/MODEL_CARD.md`.

Raw neural probabilities are not release confidence. The existing independent
outcome calibration, exact-state coverage, shadow evidence, comparison sample,
and demotion gates remain authoritative. The deterministic `exact` engine is
an explicit internal reference for unit tests; the product CLI does not expose
an engine selector.

Apache-2.0 source attribution, upstream notices, original source hashes, and
pinned model file hashes are included in the private model package. Historical
benchmark files under `docs/evidence` describe their original runtime; they are
not silently relabeled as measurements of this integration.
