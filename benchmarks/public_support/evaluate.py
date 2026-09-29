"""Public BANKING77 intent replay; never represented as production outcomes."""

import argparse
import csv
import hashlib
import io
import json
import math
import urllib.request
from pathlib import Path

from microloop import DecisionSite
from microloop.internal.engines import DecisionModelEngine

REVISION = "57ec275d8078af65b7731c2a98be812d844a6d6b"
BASE = f"https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/{REVISION}/"
CHOICES = ("card_arrival", "cash_withdrawal_charge", "card_payment_wrong_exchange_rate")
THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95)


def lower(correct, total, alpha=0.05):
    return (
        max(0.0, correct / total - math.sqrt(math.log(1 / alpha) / (2 * total))) if total else 0.0
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a fresh output directory")
    args.output.mkdir(parents=True)
    hashes = {}
    datasets = {}
    for filename in ("banking_data/train.csv", "banking_data/test.csv", "LICENSE"):
        data = urllib.request.urlopen(BASE + filename, timeout=60).read()
        hashes[filename] = hashlib.sha256(data).hexdigest()
        (args.output / Path(filename).name).write_bytes(data)
        if filename.endswith(".csv"):
            datasets[Path(filename).stem] = list(csv.DictReader(io.StringIO(data.decode())))
    site = DecisionSite("public.banking77.intent", {"text": "string"}, CHOICES)
    engine = DecisionModelEngine(
        instructions=(
            "Classify the banking support request: card_arrival means tracking delivery of a card; "
            "cash_withdrawal_charge means an ATM withdrawal fee; "
            "card_payment_wrong_exchange_rate means a card purchase currency conversion rate issue."
        )
    )
    # Fixed instructions and no fitted examples; train split is used only for calibration.
    payload = engine.compile(site, [{"state": {"text": ""}, "choice": CHOICES[0]}])
    seen = set()
    reports = {}
    predictions = {}
    frozen_threshold = None
    for split in ("train", "test"):
        selected = [r for r in datasets[split] if r["category"] in CHOICES]
        rows = []
        for row in selected:
            key = " ".join(row["text"].lower().split())
            if key in seen:
                continue  # Duplicate text cannot inflate evidence or cross split boundaries.
            seen.add(key)
            choice, probability = engine.predict(payload, {"text": row["text"]})
            rows.append(
                {
                    "text_sha256": hashlib.sha256(key.encode()).hexdigest(),
                    "expected": row["category"],
                    "choice": choice,
                    "probability": probability,
                    "correct": choice == row["category"],
                }
            )
        predictions[split] = rows
        if split == "train":
            experiments = []
            for threshold in THRESHOLDS:
                accepted = [r for r in rows if r["probability"] >= threshold]
                correct = sum(r["correct"] for r in accepted)
                # Correct for selecting among six thresholds on this calibration split.
                bound = lower(correct, len(accepted), 0.05 / len(THRESHOLDS))
                experiments.append(
                    {"threshold": threshold, "support": len(accepted), "quality_lower": bound}
                )
                if bound >= 0.95 and frozen_threshold is None:
                    frozen_threshold = threshold
            profile = {
                "min_quality": 0.95,
                "confidence_level": 0.95,
                "threshold": frozen_threshold,
                "calibration": experiments,
                "automatic_promotion": False,
                "reason": "Intent labels are proxies; no paired fallback business outcomes",
            }
            (args.output / "frozen-profile.json").write_text(json.dumps(profile, indent=2) + "\n")
        correct = sum(r["correct"] for r in rows)
        reports[split] = {
            "samples": len(rows),
            "correct": correct,
            "accuracy": correct / len(rows) if rows else None,
            "quality_lower": lower(correct, len(rows)),
            "duplicates_excluded": len(selected) - len(rows),
        }
    report = {
        "provenance": "Public BANKING77 expert-labelled intent benchmark, not customer production",
        "source": BASE,
        "license": "CC-BY-4.0",
        "source_sha256": hashes,
        "choices": CHOICES,
        "model": payload["model"],
        "runtime": payload["runtime_version"],
        "weights_sha256": payload["manifest"]["model.safetensors"],
        "profile": profile,
        "splits": reports,
        "exact_state_fast_path_coverage_on_unique_test_queries": 0.0,
        "limitation": ("Unique texts lack repeated exact-state outcome support; "
                       "no production qualification; customer/task identities unavailable"),
    }
    (args.output / "predictions.json").write_text(json.dumps(predictions, indent=2) + "\n")
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
