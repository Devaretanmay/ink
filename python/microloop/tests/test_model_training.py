"""Owned-weights pipeline: lineage rules without requiring a checkpoint."""

import importlib.util
import json

import pytest
from microloop.internal.model import training


def _rows(n=6):
    return [
        {
            "state": {"request": f"case {i} refund please"},
            "choices": ["refund", "specialist"],
            "choice": "refund" if i % 2 == 0 else "specialist",
        }
        for i in range(n)
    ]


def test_dataset_digest_stable_and_sensitive():
    rows = _rows()
    assert training.dataset_digest(rows) == training.dataset_digest(list(rows))
    changed = [dict(r, choice="refund") for r in rows]
    assert training.dataset_digest(changed) != training.dataset_digest(rows)


def test_split_keeps_untouched_held_out():
    train, held = training.split_rows(_rows(10), seed=7)
    assert len(train) + len(held) == 10 and len(held) >= 1
    train2, held2 = training.split_rows(_rows(10), seed=7)
    assert [r["state"] for r in held] == [r["state"] for r in held2]
    assert {r["state"]["request"] for r in train}.isdisjoint({r["state"]["request"] for r in held})


def test_split_rejects_tiny_data():
    with pytest.raises(ValueError, match="at least 5"):
        training.split_rows(_rows(4))


def test_validate_row_rejects_bad_labels():
    with pytest.raises(ValueError, match="one of its choices"):
        training.validate_row({"state": {}, "choices": ["a", "b"], "choice": "c"})
    with pytest.raises(ValueError, match="unique"):
        training.validate_row({"state": {}, "choices": ["a", "a"], "choice": "a"})


needs_weights = pytest.mark.skipif(
    importlib.util.find_spec("mlx") is None,
    reason="MLX unavailable",
)


@needs_weights
def test_finetune_smoke_produces_owned_lineage(tmp_path):
    from microloop.internal.model.registry import model_path

    base = model_path()
    if not (base / "model.safetensors").is_file():
        pytest.skip("Managed checkpoint unavailable")
    card = training.finetune(
        str(base), _rows(8), tmp_path / "owned", steps=2, batch_size=2, lr=1e-5, seed=7
    )
    assert card["weights_modified"] is True
    assert card["trained_by"] == "microloop-finetune-v1"
    assert card["trainable"] == ["head.", "scorer."]
    assert (tmp_path / "owned" / "microloop-model.json").is_file()
    assert (tmp_path / "owned" / "model.safetensors").is_file()
    assert "candidate only" in card["qualifies_as"]
    stored = json.loads((tmp_path / "owned" / "microloop-model.json").read_text())
    assert stored["dataset_digest"] == card["dataset_digest"]
