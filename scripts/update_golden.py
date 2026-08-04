#!/usr/bin/env python3
"""Regenerate the committed extraction golden files.

Run this only when an extraction change is intended, and read the resulting
diff before committing — that diff is the regression signal.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import FIXTURE_NAMES, FIXTURES, GOLDEN  # noqa: E402
from recheck.paper import extract_tables  # noqa: E402


def main() -> int:
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name in FIXTURE_NAMES:
        paper = extract_tables((FIXTURES / f"{name}.tex").read_text())
        target = GOLDEN / f"{name}.json"
        target.write_text(json.dumps(paper.to_dict(), indent=2, ensure_ascii=False) + "\n")
        numeric = sum(1 for t in paper.tables for c in t.cells if c.is_numeric)
        print(f"{name}: {len(paper.tables)} tables, {numeric} numeric cells → {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
