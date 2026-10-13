"""Canonical Ink Policy Model Registry and Specifications."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from .constants import (
    INK_DECISION_LARGE,
    INK_DECISION_SMALL,
    LEGACY_DECISION_V1,
    LEGACY_INK_DECISION_V1,
    MODEL_RUNTIME_VERSION,
    resolve_policy_model_id,
)


@dataclass(frozen=True)
class ModelSpec:
    canonical_id: str
    aliases: list[str]
    family: str
    architecture: str
    model_version: str
    runtime_version: str
    checkpoint_revision: str
    parameter_count: int
    weight_dtype: str
    backend: str
    hardware_support: list[str]
    optional: bool
    license: str
    repository: str
    provenance: dict[str, Any]
    sha256: dict[str, str]
    size_tier: str = "small"
    is_default: bool = False

    @property
    def manifest_sha256(self) -> str:
        canonical_content = json.dumps(self.to_dict(), sort_keys=True).encode("utf-8")
        return hashlib.sha256(canonical_content).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["name"] = self.canonical_id
        d["model_id"] = self.canonical_id
        d["version"] = self.model_version
        return d


SMALL_SPEC = ModelSpec(
    canonical_id=INK_DECISION_SMALL,
    aliases=[LEGACY_INK_DECISION_V1, LEGACY_DECISION_V1, "small"],
    family="ink-decision",
    architecture="ModernBERT-large (28L/16H) + distilled DecisionHead + Scorer + act_head",
    model_version="1.0.0",
    runtime_version=MODEL_RUNTIME_VERSION,
    checkpoint_revision="phase18-full-kd",
    parameter_count=421293830,
    weight_dtype="float16",
    backend="mlx",
    hardware_support=["Apple Silicon (Metal/GPU)", "Linux (CPU/MLX)"],
    optional=False,
    license="Ink Internal / Pinned (see NOTICE)",
    repository="ink/ink-decision-small",
    provenance={
        "base_encoder": "ModernBERT-large",
        "prior_checkpoint": "ink-decision-v1",
        "lineage": "ModernBERT-large -> ink-decision-v1 -> Phase 18 Stage 1 -> Phase 18 Stage 2 -> Phase 18 Full KD -> ink-decision-small 1.0.0",
        "adaptation": "Phase 18 Full Knowledge Distillation (top-2 encoder unfreeze)",
        "teacher": "ink-decision-large (fastino/GLiNER2.5-Decide, Apache-2.0)",
        "training_objective": "0.7*CE(verified) + 0.3*tau^2*KL(teacher || student)",
        "temperature_schedule": "tau = 4 for steps 1-300, tau = 2 for steps 301-600",
        "teacher_disagreement_weight": "KL contribution * 0.5",
        "training_steps": 600,
        "batch_size": 16,
        "learning_rate": "5e-5",
        "trainable": "DecisionHead + top-2 encoder layers",
        "training_rows": 5598,
        "final_loss": 0.2474,
        "support_accuracy": 0.950,
        "support_auroc": 0.9386,
        "multi_domain_mean_accuracy": 0.7176,
        "ood_alien_auroc": 0.9938,
    },
    sha256={
        "model.safetensors": "28e7e65bfbd272c6392c52616bbb409a7a425db604df442e2682ab0155593276",
        "rl_agent_config.json": "f1ee15bdcc840e91f649e6eefe2c7dc3d1c7779cecc66215a01fccc10c02de08",
        "encoder/config.json": "bf3ab80598fdccf414855a2ce80f22859e4492d06ca8a62ddd1cfb63972f8979",
        "tokenizer/tokenizer.json": "6c8aaa9a542084f2457eab775d4eeb51f92a70c0fd9de28d5edb0ddec3c08d30",
        "tokenizer/tokenizer_config.json": "50044de60daaa73df97d262e15a40d4faf0160e7d742df64b377877a1320dd12",
    },
    size_tier="small",
    is_default=True,
)

LARGE_SPEC = ModelSpec(
    canonical_id=INK_DECISION_LARGE,
    aliases=["large"],
    family="ink-decision",
    architecture="microsoft/deberta-v3-large + GLiNER2.5 Decide multi-label head",
    model_version="1.0.0",
    runtime_version=MODEL_RUNTIME_VERSION,
    checkpoint_revision="gliner2.5-decide-486m",
    parameter_count=486444053,
    weight_dtype="float32",
    backend="gliner_torch",
    hardware_support=["Apple Silicon (CPU/MPS)", "Linux (CPU/CUDA)", "macOS (CPU)"],
    optional=True,
    license="Apache-2.0 (see NOTICE & LICENSE)",
    repository="ink/ink-decision-large",
    provenance={
        "base_model": "microsoft/deberta-v3-large",
        "upstream_checkpoint": "fastino/GLiNER2.5-Decide",
        "license": "Apache-2.0",
        "attribution": "fastino/GLiNER2.5-Decide published by Fastino under Apache-2.0",
        "adaptation_status": "authorized upstream checkpoint retained unchanged (no cosmetic perturbation)",
        "role": "optional high-capability semantic proposal and teacher model",
        "mean_accuracy": 0.795,
        "correctness_auroc": 0.869,
    },
    sha256={
        "model.safetensors": "40a5a23ff860dc3dff426cecd1048cacdd29c648c96db209dad818e9686dc997",
        "config.json": "501c6579278518e72dbba5a1f248b421dd5bfb13600db45d86c94c224368670b",
        "special_tokens_map.json": "84ea70143f533d7e99b393d87f20010887a9ac2cba955828ef313886e4e83f4f",
        "tokenizer.json": "3ad87d9ffe669147063e70850927dd2da90249e2acc5c8527f1eb65df467bcc8",
        "tokenizer_config.json": "323199a4e946039410899f3779f2aa3eaef1500213c512727ad0f623d4f21309",
        "encoder_config/config.json": "bd32f1484ba5a199f7a63df44df3814b839fffcf6e64478323c4689868ef6015",
    },
    size_tier="large",
    is_default=False,
)

POLICY_MODEL_REGISTRY: dict[str, ModelSpec] = {
    INK_DECISION_SMALL: SMALL_SPEC,
    INK_DECISION_LARGE: LARGE_SPEC,
}


def get_model_spec(model_id_or_alias: str) -> ModelSpec:
    """Retrieve canonical model specification by canonical ID or alias."""
    try:
        resolved = resolve_policy_model_id(model_id_or_alias)
    except ValueError as exc:
        raise KeyError(f"No registered model specification for '{model_id_or_alias}'") from exc
    if resolved and resolved in POLICY_MODEL_REGISTRY:
        return POLICY_MODEL_REGISTRY[resolved]
    raise KeyError(f"No registered model specification for '{model_id_or_alias}'")
