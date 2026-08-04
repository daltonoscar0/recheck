#!/usr/bin/env python3
"""Regenerate the committed calibration results with the real executor.

This used to be a hand-written stand-in that knew the garden-path repo's CSV
layout. It no longer knows anything about it: it runs the same pipeline
`recheck run` does — acquire read-only, map, budget, execute, harvest — and
writes what comes out. If the committed fixture moves, the executor changed.

    python scripts/build_calibration_results.py --repo ~/price-of-reanalysis \
        --out tests/fixtures/results/garden_path_results.json

The repo is not in CI, so the fixture stays committed; this script is how it is
refreshed, and the resulting diff is the review artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from recheck.exec import Budget, LocalSandbox, RunOptions, execute  # noqa: E402
from recheck.paper import extract_tables  # noqa: E402
from recheck.repo import RepoError, acquire  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.home() / "price-of-reanalysis")
    parser.add_argument(
        "--paper",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "garden_path_calibration.tex",
        help="LaTeX source of the calibration paper.",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-hours", type=float, default=2.0)
    parser.add_argument("--max-gpu", type=float, default=0.0)
    parser.add_argument("--max-download-mb", type=float, default=512.0)
    args = parser.parse_args()

    paper = extract_tables(args.paper.read_text())
    root = Path(tempfile.mkdtemp(prefix="recheck-calibration-"))
    sandbox = LocalSandbox(root)
    try:
        repo = acquire(str(args.repo), sandbox.workdir)
    except RepoError as exc:
        parser.error(str(exc))

    report = execute(
        paper,
        repo,
        sandbox,
        RunOptions(
            budget=Budget(
                max_hours=args.max_hours,
                max_gpu_hours=args.max_gpu,
                max_download_mb=args.max_download_mb,
            )
        ),
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report.results.to_dict(), indent=2, ensure_ascii=False) + "\n"
    )
    produced = sum(1 for cell in report.results.cells if not cell.is_failure)
    print(f"wrote {len(report.results.cells)} result cells ({produced} with values) → {args.out}")
    for outcome in report.outcomes:
        codes = ", ".join(b.code.value for b in outcome.blockers) or "—"
        print(f"  {outcome.table_id}: {outcome.status} via {outcome.script or '—'} [{codes}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
