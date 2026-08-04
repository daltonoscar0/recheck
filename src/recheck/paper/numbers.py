"""Numeric interpretation of a single table cell.

A cell counts as numeric only when what remains after stripping units, markers
and uncertainty is *entirely* a number. Requiring a full match is what keeps
"GPT-2-large" from being read as the number 2 — the failure mode that would
silently corrupt every downstream comparison.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..schema import UncertaintyKind, Unit
from .latex import flatten

_NUM = r"[-+−]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"

_RE_PLAIN = re.compile(rf"^({_NUM})$")
_RE_PM = re.compile(rf"^({_NUM})\s*±\s*({_NUM})$")
_RE_PAREN = re.compile(rf"^({_NUM})\s*\(\s*({_NUM})\s*\)$")
_RE_SUBSCRIPT = re.compile(rf"({_NUM})\s*_\s*\{{\s*({_NUM})\s*\}}")

_MARKER_CHARS = "*∗†‡§¶"
_COMPARATORS = ("<=", ">=", "≤", "≥", "<", ">", "≈", "~")

_NON_NUMERIC_TEXT = {"", "-", "--", "–", "—", "n/a", "na", "n.a.", "nan", "none", "?"}


@dataclass
class ParsedNumber:
    is_numeric: bool
    text: str
    value: float | None = None
    uncertainty: float | None = None
    uncertainty_kind: UncertaintyKind = UncertaintyKind.NONE
    unit: Unit = Unit.NONE
    markers: list[str] | None = None

    def __post_init__(self) -> None:
        if self.markers is None:
            self.markers = []


def _to_float(raw: str) -> float:
    return float(raw.replace("−", "-"))


def parse_cell(raw: str) -> ParsedNumber:
    """Interpret one cell's LaTeX source."""
    text = flatten(raw)

    # Subscript uncertainty must be read before flattening loses the braces.
    subscript = _RE_SUBSCRIPT.search(raw)

    core = text
    markers: list[str] = []
    unit = Unit.NONE

    if "%" in core:
        unit = Unit.PERCENT
        core = core.replace("%", "")
    if "×" in core:
        unit = Unit.TIMES
        core = core.replace("×", "")

    for comparator in _COMPARATORS:
        if core.lstrip().startswith(comparator):
            markers.append(comparator)
            core = core.lstrip()[len(comparator) :]
            break

    stripped = core.rstrip()
    while stripped and stripped[-1] in _MARKER_CHARS:
        markers.insert(0, stripped[-1])
        stripped = stripped[:-1].rstrip()
    core = stripped

    core = core.replace(",", "") if re.fullmatch(r"[\d,]+(?:\.\d+)?", core.strip()) else core
    core = re.sub(r"\s+", " ", core).strip()

    if core.lower() in _NON_NUMERIC_TEXT:
        return ParsedNumber(is_numeric=False, text=text, unit=Unit.NONE, markers=markers)

    if subscript is not None:
        return ParsedNumber(
            is_numeric=True,
            text=text,
            value=_to_float(subscript.group(1)),
            uncertainty=abs(_to_float(subscript.group(2))),
            uncertainty_kind=UncertaintyKind.SUBSCRIPT,
            unit=unit,
            markers=markers,
        )

    match = _RE_PM.match(core)
    if match:
        return ParsedNumber(
            is_numeric=True,
            text=text,
            value=_to_float(match.group(1)),
            uncertainty=abs(_to_float(match.group(2))),
            uncertainty_kind=UncertaintyKind.PM,
            unit=unit,
            markers=markers,
        )

    match = _RE_PAREN.match(core)
    if match:
        return ParsedNumber(
            is_numeric=True,
            text=text,
            value=_to_float(match.group(1)),
            uncertainty=abs(_to_float(match.group(2))),
            uncertainty_kind=UncertaintyKind.PAREN,
            unit=unit,
            markers=markers,
        )

    match = _RE_PLAIN.match(core)
    if match:
        return ParsedNumber(
            is_numeric=True,
            text=text,
            value=_to_float(match.group(1)),
            uncertainty_kind=UncertaintyKind.NONE,
            unit=unit,
            markers=markers,
        )

    return ParsedNumber(is_numeric=False, text=text, markers=markers)
