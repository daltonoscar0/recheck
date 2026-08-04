"""Diff side: compare paper cells to fresh results and render the report."""

from .config import ConfigError, discover_config, load_tolerance_config, resolve_policy
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
    "ConfigError",
    "discover_config",
    "load_tolerance_config",
    "resolve_policy",
    "render_markdown",
    "render_terminal",
    "FailureCode",
    "TaxonomyError",
]
