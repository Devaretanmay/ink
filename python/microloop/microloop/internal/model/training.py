"""Microloop-owned weight training.

Fine-tunes the decision head + scorer of a Microloop Decision checkpoint on
labeled (state, choices, choice) rows and writes a new integrity-bound
checkpoint directory with its own lineage card.

What this is: the mechanism by which Microloop earns its own weights. The
shipped default stays pinned to base values until a trained checkpoint passes
the normal lifecycle (compile → calibrate → evaluate → shadow) on customer
outcome data. Training accuracy reported here is never qualification.

Ownership rule: only checkpoints whose `microloop-model.json` has
`weights_modified: true` and a Microloop training lineage may be presented as
Microloop-trained. Anything else is the pinned base, whatever the directory
is called.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
from pathlib import Path

from .agent import Agent, collate_items

TRAINABLE_PREFIXES = ("head.", "scorer.")
HELD_OUT_FRACTION = 0.2


def dataset_digest(rows) -> str:
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def split_rows(rows, *, seed=7):
    """Chronology-agnostic smoke split: train bulk + untouched held-out slice."""
    rows = list(rows)
    if len(rows) < 5:
        raise ValueError("Need at least 5 labeled rows to train with a held-out slice")
    rng = random.Random(seed)
    held_n = max(1, int(len(rows) * HELD_OUT_FRACTION))
    held_idx = set(rng.sample(range(len(rows)), held_n))
    return [r for i, r in enumerate(rows) if i not in held_idx], [
        r for i, r in enumerate(rows) if i in held_idx
    ]


def validate_row(row):
    state, choices, choice = row["state"], list(row["choices"]), row["choice"]
    if not choices or len(set(choices)) != len(choices):
        raise ValueError("Choices must be a nonempty unique list")
    if choice not in choices:
        raise ValueError("Row choice must be one of its choices")
    if not isinstance(state, dict):
        raise ValueError("Row state must be a JSON object")
    return state, choices, choice


def _encode(agent, row, instructions):
    state, choices, choice = validate_row(row)
    ins = row.get("instructions") if isinstance(row, dict) else None
    question = {
        "decision": {
            "type": "choice",
            "criteria": choices,
            "instructions": ins or instructions or "Choose the next action.",
        }
    }
    items, _ = agent.prepare(state, question)
    return items[0], choices.index(choice), len(choices)


def accuracy(agent, rows, instructions=None) -> float:
    hits = 0
    for row in rows:
        state, choices, choice = validate_row(row)
        ins = row.get("instructions") if isinstance(row, dict) else None
        out = agent.predict(
            state,
            {
                "decision": {
                    "type": "choice",
                    "criteria": choices,
                    "instructions": ins or instructions or "Choose the next action.",
                }
            },
        )["answers"]["decision"]
        hits += out["choice"] == choice
    return hits / len(rows)


def finetune(
    base_checkpoint=None,
    rows=None,
    output_dir=None,
    *,
    eval_rows=None,
    instructions=None,
    steps=50,
    batch_size=4,
    lr=1e-4,
    seed=7,
):
    """Train head+scorer, write a new checkpoint dir, return its lineage card."""
    import mlx.core as mx  # Training deps loaded only when fine-tuning is invoked.
    import mlx.nn as nn
    from mlx.optimizers import Adam

    if not rows:
        raise ValueError("Training needs labeled rows")
    if output_dir is None:
        raise ValueError("Training needs an explicit output_dir")
    if eval_rows is not None:
        train_rows, held_rows = list(rows), list(eval_rows)
    else:
        train_rows, held_rows = split_rows(rows, seed=seed)
    agent = Agent(base_checkpoint or _managed(), dtype="float32")
    before = accuracy(agent, held_rows, instructions)

    encoded = [_encode(agent, r, instructions) for r in train_rows]
    agent.model.train()
    agent.model.freeze()
    agent.model.head.unfreeze()
    agent.model.scorer.unfreeze()
    optimizer = Adam(learning_rate=lr)

    def loss_fn(model, input_ids, attention_mask, marker_pos, marker_mask, qtype, targets):
        logits, _ = model(input_ids, attention_mask, marker_pos, marker_mask, qtype)
        log_p = logits - mx.logsumexp(logits, axis=-1, keepdims=True)
        nll = -log_p[mx.arange(len(targets)), targets]
        return nll.mean()

    loss_and_grad = nn.value_and_grad(agent.model, loss_fn)
    rng = random.Random(seed)
    last_loss = None
    for _ in range(steps):
        batch = [encoded[i] for i in (rng.randrange(len(encoded)) for _ in range(batch_size))]
        collated = collate_items(
            [{"ids": item[0]["ids"], "markers": item[0]["markers"], "qtype": 0} for item in batch],
            agent.tok.pad_token_id,
        )
        targets = mx.array([t for _, t, _ in batch], dtype=mx.int32)
        with mx.stream(agent.device):
            tensors = {k: mx.array(v) for k, v in collated.items()}
            loss, grads = loss_and_grad(agent.model, *tensors.values(), targets)
            optimizer.update(agent.model, grads)
            mx.eval(agent.model.parameters(), optimizer.state)
            last_loss = float(loss)
        if last_loss is None or last_loss != last_loss:  # NaN guard
            raise FloatingPointError("Training diverged; lower lr and retry")
    agent.model.eval()

    after = accuracy(agent, held_rows, instructions)
    out = Path(output_dir).expanduser().resolve()
    if out.exists():
        raise FileExistsError(f"Refusing to overwrite existing checkpoint: {out}")
    out.mkdir(parents=True)
    base = Path(agent.model_dir)
    for name in ("encoder", "tokenizer"):
        src = base / name
        dst = out / name
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copyfile(src, dst)
    rl_cfg = json.loads((base / "rl_agent_config.json").read_text())
    if "temperature_by_options" in rl_cfg:
        rl_cfg["temperature_by_options"]["choice:11+"] = 1.0
    rl_cfg["model_name"] = "microloop-decision-v1"
    (out / "rl_agent_config.json").write_text(json.dumps(rl_cfg, indent=2) + "\n")
    import mlx.utils
    agent.model.update(mlx.utils.tree_map(lambda x: x.astype(mx.float16), agent.model.parameters()))
    agent.model.save_weights(str(out / "model.safetensors"))
    card = {
        "name": "microloop-decision-v1",
        "version": "1.0.0",
        "architecture": "ModernBERT-large + DecisionHead (2 layers) + Scorer (2 layers)",
        "base_model": "aac6fef/laya-mlx",
        "base_revision": "20aed815fc6acde75733882e7ec0e3f28aeb9717",
        "weights_modified": True,
        "trained_by": "microloop-finetune-v1",
        "trainable": sorted(TRAINABLE_PREFIXES),
        "base_checkpoint": str(base),
        "base_manifest": DecisionManifest(base),
        "dataset_digest": dataset_digest(
            [{k: r[k] for k in ("state", "choices", "choice")} for r in rows]
        ),
        "train_rows": len(train_rows),
        "held_rows": len(held_rows),
        "steps": steps,
        "batch_size": batch_size,
        "learning_rate": lr,
        "seed": seed,
        "held_accuracy_before": round(before, 4),
        "held_accuracy_after": round(after, 4),
        "final_loss": round(float(last_loss), 4),
        "supported_primitives": ["choice"],
        "parameter_count": 421293830,
        "trainable_parameter_count": 26245121,
        "sha256": FileHashes(out),
        "qualifies_as": "candidate only; lifecycle qualification still required",
    }
    (out / "microloop-model.json").write_text(json.dumps(card, indent=2) + "\n")
    return card


def DecisionManifest(root) -> dict:
    root = Path(root)
    result = {}
    for name in ("model.safetensors", "rl_agent_config.json", "encoder/config.json"):
        result[name] = _sha(root / name)
    for path in sorted((root / "tokenizer").glob("*")):
        result[str(path.relative_to(root))] = _sha(path)
    return result


def FileHashes(root) -> dict:
    return DecisionManifest(root)


def _sha(path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _managed():
    from .registry import model_path  # Deferred: only needed for default checkpoint lookup.

    return str(model_path())
