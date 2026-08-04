#!/usr/bin/env python3
"""Recompute the calibration table's numbers from the source repo's raw data.

This stands in for the execution agent that milestone 2 will provide: it reads
the garden-path repo's per-item surprisal CSVs, aggregates them the way the
paper does, and emits a results.json. The repo is opened read-only.

    python scripts/build_calibration_results.py --repo ~/price-of-reanalysis \
        --out tests/fixtures/results/garden_path_results.json
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
from collections import defaultdict
from pathlib import Path

MODELS = {
    "GPT-2-large": "surprisal_gpt2-large.csv",
    "Pythia-1.4B": "surprisal_EleutherAI_pythia-1.4b.csv",
}

CONDITIONS = {"Ambiguous": "ambiguous", "Control": "control"}

# Reported in the paper but not reproducible here: the model is API-only, so no
# checkpoint exists to score the stimuli against.
UNRUNNABLE_MODEL = "GPT-3 davinci"
UNRUNNABLE_EVIDENCE = (
    "openai/davinci is API-gated and no local checkpoint is published; "
    "the repo contains no scoring path for it (score_surprisal_multimodel.py "
    "enumerates only HuggingFace model ids)"
)


def aggregate(csv_path: Path) -> dict[str, list[float]]:
    groups: dict[str, list[float]] = defaultdict(list)
    with csv_path.open() as handle:
        for row in csv.DictReader(handle):
            if row["construction"] != "NPZ":
                continue
            groups[row["condition"]].append(float(row["critical_region_surprisal"]))
    return groups


def git_commit(repo: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.home() / "price-of-reanalysis")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    stimuli = args.repo / "phase1_stimuli"
    if not stimuli.is_dir():
        parser.error(f"no phase1_stimuli directory under {args.repo}")

    cells = []
    for model, filename in MODELS.items():
        groups = aggregate(stimuli / filename)
        means = {}
        for label, key in CONDITIONS.items():
            values = groups.get(key, [])
            if not values:
                continue
            mean = statistics.mean(values)
            means[label] = mean
            cells.append(
                {
                    "address": f"Table 1 › {model} › {label}",
                    "value": round(mean, 3),
                    "uncertainty": round(statistics.stdev(values), 3),
                    "n_seeds": 1,
                }
            )
        if len(means) == 2:
            cells.append(
                {
                    "address": f"Table 1 › {model} › Δ",
                    "value": round(means["Ambiguous"] - means["Control"], 3),
                }
            )

    for label in (*CONDITIONS, "Δ"):
        cells.append(
            {
                "address": f"Table 1 › {UNRUNNABLE_MODEL} › {label}",
                "failure": {"code": "MISSING_CHECKPOINT", "evidence": UNRUNNABLE_EVIDENCE},
            }
        )

    payload = {
        "schema_version": "1.0",
        "run": {
            "repo": str(args.repo),
            "commit": git_commit(args.repo),
            "note": "aggregated from committed per-item surprisal CSVs, not re-scored",
        },
        "cells": cells,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(cells)} result cells → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
