#!/usr/bin/env bash
# Demo: extract tables from LaTeX fixtures and diff them against results.
#
#   ./scripts/demo.sh            # both demos, coloured terminal report
#   ./scripts/demo.sh --markdown # also write the markdown artifacts
#
# Exit status is not checked here on purpose: `recheck diff` exits 1 when any
# cell is RED, and the second demo has RED cells by design.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FIXTURES="tests/fixtures"
RESULTS="$FIXTURES/results"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

WRITE_MARKDOWN=false
[ "${1:-}" = "--markdown" ] && WRITE_MARKDOWN=true

run() { echo; echo "\$ $*"; echo; "$@"; }

echo "════════════════════════════════════════════════════════════════════"
echo " 1. Calibration — the garden-path paper's real numbers"
echo "════════════════════════════════════════════════════════════════════"
echo
echo "Paper values transcribed from the Price of Reanalysis phase-1 tables."
echo "Rerun values produced by the executor against the real repo — see the"
echo "provenance line for what ran and what was refused. Regenerate with:"
echo "  python scripts/build_calibration_results.py --repo ~/price-of-reanalysis \\"
echo "      --out $RESULTS/garden_path_results.json"

run uv run recheck extract "$FIXTURES/garden_path_calibration.tex" --out "$WORK/calibration.json"
run uv run recheck diff "$WORK/calibration.json" "$RESULTS/garden_path_results.json"

echo
echo "════════════════════════════════════════════════════════════════════"
echo " 2. Full spectrum — every status the diff engine can emit"
echo "════════════════════════════════════════════════════════════════════"

run uv run recheck extract "$FIXTURES/demo_report.tex" --out "$WORK/demo.json"
run uv run recheck diff "$WORK/demo.json" "$RESULTS/demo_results.json"

if [ "$WRITE_MARKDOWN" = true ]; then
  mkdir -p reports
  uv run recheck diff "$WORK/calibration.json" "$RESULTS/garden_path_results.json" \
    --out reports/calibration.md >/dev/null
  uv run recheck diff "$WORK/demo.json" "$RESULTS/demo_results.json" \
    --out reports/demo.md >/dev/null
  echo
  echo "Markdown artifacts written to reports/"
fi
