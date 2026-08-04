#!/usr/bin/env python3
"""Run extraction across a corpus of real arXiv papers and flag anomalies.

Fixtures encode what their author already thought could go wrong. Every
extraction bug found so far — layout tabulars consuming table numbers, line
breaks landing in addresses, `\\epsilon_{ls}` losing its symbol, `$n$-gram`
splitting in two — was invisible to the fixtures and obvious the first time
real papers went through. This keeps that check repeatable.

    python scripts/sweep_papers.py                 # the default corpus
    python scripts/sweep_papers.py --papers ids.txt --out sweep.json

Exits non-zero if any paper crashes or produces a structurally broken address,
so it can gate a release. Requires network; it is deliberately not part of the
unit suite.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from recheck.paper import extract_tables, fetch, find_main_tex, resolve_inputs  # noqa: E402
from recheck.schema import ADDRESS_SEP, INLINE_LABEL  # noqa: E402

#: A spread of table styles: booktabs, multi-level headers, macro-defined
#: headers, appendix-heavy papers, and papers whose numbers are scientific
#: notation. Add to it whenever a new paper breaks something.
DEFAULT_CORPUS = [
    "1706.03762",  # Attention Is All You Need — macro headers, sci-notation costs
    "1810.04805",  # BERT
    "2005.14165",  # GPT-3 — 60+ layout tabulars in the appendix
    "1907.11692",  # RoBERTa
    "1910.10683",  # T5 — very wide tables
    "2005.03692",  # SyntaxGym — the calibration paper for the mapper
    "1912.00582",  # BLiMP
    "2104.08691",  # Prompt tuning
    "2203.02155",  # InstructGPT — instruction boxes typeset as tabulars
    "1802.05365",  # ELMo
    "2212.12131",  # Surprisal and reading times
    "1808.09121",  # Targeted syntactic evaluation
]

#: Anomalies that mean extraction is broken, as opposed to merely unusual.
FATAL = ("crash", "empty table emitted", "address with no path", "malformed whitespace")


def anomalies(paper) -> list[str]:
    out: list[str] = []
    for table in paper.tables:
        numeric = [c for c in table.cells if c.is_numeric]
        if not table.cells:
            out.append(f"{table.id}: empty table emitted")
        if not table.caption:
            out.append(f"{table.id}: no caption")
        if table.cells and not numeric:
            out.append(f"{table.id}: {len(table.cells)} cells, none numeric")
        duplicates = len(table.cells) - len({c.address for c in table.cells})
        if duplicates:
            out.append(f"{table.id}: {duplicates} duplicate addresses")
        for cell in table.cells:
            if ADDRESS_SEP not in cell.address:
                out.append(f"{table.id}: address with no path: {cell.address!r}")
            if cell.address != cell.address.strip() or "  " in cell.address:
                out.append(f"{table.id}: malformed whitespace: {cell.address!r}")
            if "\\" in cell.address:
                out.append(f"{table.id}: latex left in address: {cell.address!r}")
        for cell in numeric:
            if cell.value is None:
                out.append(f"{table.id}: numeric cell with no value: {cell.address!r}")
            if cell.uncertainty is not None and cell.uncertainty < 0:
                out.append(f"{table.id}: negative uncertainty: {cell.address!r}")
    return out


def sweep_one(arxiv_id: str) -> dict:
    entry: dict = {"id": arxiv_id}
    try:
        source = fetch(arxiv_id)
        tex = source.tex_files()
        if not tex:
            entry["error"] = "e-print contains no .tex"
            return entry
        main = find_main_tex(tex) or sorted(tex)[0]
        document = resolve_inputs(tex[main], lambda name: _member(tex, name))
        paper = extract_tables(document, source={"arxiv_id": arxiv_id})

        inline = [
            t
            for t in paper.tables
            if any(c.address.startswith(INLINE_LABEL) for c in t.cells)
        ]
        entry.update(
            floats=len(paper.tables) - len(inline),
            inline=len(inline),
            source_floats=document.count(r"\begin{table"),
            numeric=sum(1 for t in paper.tables for c in t.cells if c.is_numeric),
            anomalies=anomalies(paper),
        )
        # Float numbering must match the paper's own, or every address is
        # pointing at a different table than the one it names.
        if entry["floats"] != entry["source_floats"]:
            entry["anomalies"].append(
                f"numbering drift: {entry['floats']} floats extracted, "
                f"{entry['source_floats']} in source"
            )
    except Exception as exc:  # noqa: BLE001 - a crash is the headline result
        entry["crash"] = f"{type(exc).__name__}: {exc}"
        entry["traceback"] = traceback.format_exc().splitlines()[-8:]
    return entry


def _member(files: dict[str, str], name: str) -> str | None:
    for candidate in (name, f"{name}.tex", f"{name}.ltx"):
        if candidate in files:
            return files[candidate]
    suffix = name.split("/")[-1]
    for key in files:
        if key.endswith(f"/{suffix}") or key.endswith(f"/{suffix}.tex"):
            return files[key]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--papers", type=Path, help="file of arXiv ids, one per line")
    parser.add_argument("--out", type=Path, default=Path("sweep.json"))
    parser.add_argument("--delay", type=float, default=3.0, help="seconds between fetches")
    args = parser.parse_args()

    corpus = (
        [line.strip() for line in args.papers.read_text().splitlines() if line.strip()]
        if args.papers
        else DEFAULT_CORPUS
    )

    report: dict[str, dict] = {}
    for index, arxiv_id in enumerate(corpus, start=1):
        entry = sweep_one(arxiv_id)
        report[arxiv_id] = entry
        headline = entry.get("crash") or (
            f"{entry.get('floats', 0)} floats / {entry.get('inline', 0)} inline, "
            f"{entry.get('numeric', 0)} numeric, {len(entry.get('anomalies', []))} anomalies"
        )
        print(f"[{index}/{len(corpus)}] {arxiv_id}: {headline}", flush=True)
        for anomaly in entry.get("anomalies", []):
            print(f"      {anomaly}", flush=True)
        if index < len(corpus):
            time.sleep(args.delay)  # arXiv asks for politeness

    args.out.write_text(json.dumps(report, indent=2) + "\n")

    fatal = [
        (arxiv_id, detail)
        for arxiv_id, entry in report.items()
        for detail in ([entry["crash"]] if "crash" in entry else entry.get("anomalies", []))
        if any(marker in detail for marker in FATAL)
    ]
    print(f"\n{len(report)} papers swept → {args.out}")
    if fatal:
        print(f"{len(fatal)} fatal:")
        for arxiv_id, detail in fatal:
            print(f"  {arxiv_id}: {detail}")
        return 1
    print("no crashes, no structurally broken addresses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
