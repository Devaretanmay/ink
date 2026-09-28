"""Public-data threshold calibration (Banking77, CC-BY-4.0).

Measures Laya agreement against versioned intent mapping on a calibration split,
freezes promotion gate thresholds, then confirms once on untouched test data.
Public provenance only; never customer traffic. macOS arm64 with laya-mlx.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import time
import urllib.request
from pathlib import Path

from microloop.internal.engines import LayaEngine
from microloop.internal.verification import lower_bound

HERE = Path(__file__).resolve().parent
TRAIN_URL = "https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/master/banking_data/train.csv"
TEST_URL = "https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/master/banking_data/test.csv"

QUESTION = {
    "type": "choice",
    "criteria": ["refund", "request_information", "specialist"],
    "instructions": (
        "Route the banking request. Choose refund only for refund or duplicate-charge "
        "requests. Choose request_information for balance or transfer status questions. "
        "Otherwise choose specialist."
    ),
}


def fetch(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file():
        with urllib.request.urlopen(url, timeout=120) as response, dest.open("wb") as out:
            out.write(response.read())
    return dest


def load_mapping():
    expected = {}
    for line in (HERE / "mapping_v1.jsonl").read_text().splitlines():
        row = json.loads(line)
        for intent in row["intents"]:
            assert intent not in expected, f"duplicate intent {intent}"
            expected[intent] = row["expected_choice"]
    return expected


def load_rows(path, expected):
    rows = []
    with open(path) as handle:
        for row in csv.DictReader(handle):
            if row["category"] in expected:
                rows.append({"text": row["text"], "choice": expected[row["category"]]})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--calibration-n", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    expected = load_mapping()
    train = load_rows(fetch(TRAIN_URL, HERE / "data" / "train.csv"), expected)
    test = load_rows(fetch(TEST_URL, HERE / "data" / "test.csv"), expected)
    rng = random.Random(args.seed)
    calibration = rng.sample(train, min(args.calibration_n, len(train)))
    engine = LayaEngine(args.checkpoint)
    examples = [{"state": {"request": r["text"]}, "choice": r["choice"]} for r in calibration[:3]]
    site = type("Site", (), {"choices": ("refund", "request_information", "specialist")})()
    payload = engine.compile(site, examples)

    def score(rows, tag):
        groups = {}
        for i, row in enumerate(rows):
            choice, _ = engine.predict(payload, {"request": row["text"]})
            hit = float(choice == row["choice"])
            groups.setdefault(row["choice"], []).append(hit)
            if (i + 1) % 250 == 0:
                progress = {"phase": tag, "completed": i + 1, "total": len(rows)}
                print(json.dumps(progress), flush=True)
        return {name: {"n": len(v), "accuracy": sum(v) / len(v), "lower": lower_bound(v)}
                for name, v in groups.items()}

    started = time.perf_counter()
    cal = score(calibration, "calibration")
    # Freeze gate from calibration evidence only.
    supported_quality = min(v["lower"] for v in cal.values())
    gate = {"min_quality": round(min(0.95, supported_quality), 4), "max_degradation": 0.01,
            "basis": "calibration_lower_bound"}
    if supported_quality < 0.95:
        gate["target_feasible"] = False
    else:
        gate["target_feasible"] = True
    ev = score(test, "evaluation")
    report = {
        "provenance": "public Banking77 (CC-BY-4.0), intent mapping v1, never customer traffic",
        "mapping": "mapping_v1.jsonl",
        "calibration_rows": len(calibration),
        "evaluation_rows": len(test),
        "calibration": cal,
        "gate": gate,
        "evaluation": ev,
        "evaluation_gate_hold": all(v["lower"] >= gate["min_quality"] for v in ev.values()),
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
