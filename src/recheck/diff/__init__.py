"""Diff side: compare paper cells to fresh results and render the report."""

from .engine import (
    CellComparison,
    DiffReport,
    Status,
    Tolerance,
    TolerancePolicy,
    compare,
    parse_tolerance,
)
from .render import render_markdown, render_terminal
from .taxonomy import FailureCode, TaxonomyError

__all__ = [
    "CellComparison",
    "DiffReport",
    "Status",
    "Tolerance",
    "TolerancePolicy",
    "compare",
    "parse_tolerance",
    "render_markdown",
    "render_terminal",
    "FailureCode",
    "TaxonomyError",
]
