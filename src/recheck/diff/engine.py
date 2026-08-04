"""Cell-by-cell comparison of paper values against fresh results."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from ..schema import Cell, Paper, ResultCell, Results, Unit
from .taxonomy import FailureCode, parse_code, validate


class Status(StrEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    UNRUNNABLE = "UNRUNNABLE"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


@dataclass(frozen=True)
class Tolerance:
    """Comparison band for one cell.

    `absolute` of None means "derive from magnitude", which keeps a 0.02 change
    meaningful for a surprisal delta near 1 without flagging noise in a table of
    perplexities in the hundreds.
    """

    relative: float = 0.05
    absolute: float | None = None
    yellow_multiplier: float = 3.0

    def band(self, reference: float) -> float:
        absolute = self.absolute if self.absolute is not None else default_absolute(reference)
        return max(absolute, abs(reference) * self.relative)


def default_absolute(reference: float) -> float:
    magnitude = abs(reference)
    if magnitude < 1:
        return 0.01
    if magnitude < 10:
        return 0.05
    if magnitude < 100:
        return 0.5
    return 1.0


@dataclass
class TolerancePolicy:
    """A default band plus per-table overrides keyed by table id or label."""

    default: Tolerance = field(default_factory=Tolerance)
    per_table: dict[str, Tolerance] = field(default_factory=dict)

    def for_table(self, table_id: str) -> Tolerance:
        return self.per_table.get(table_id, self.default)


@dataclass
class CellComparison:
    address: str
    status: Status
    paper_value: float | None
    result_value: float | None
    paper_text: str
    delta: float | None = None
    relative_delta: float | None = None
    band: float | None = None
    failure_code: FailureCode | None = None
    evidence: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def table_key(self) -> tuple[int, int]:
        """Sort key: floats first in paper order, then inline tables."""
        match = re.match(r"(Inline table|Table) (\d+)", self.address)
        if not match:
            return (2, 0)
        return (1 if match.group(1) == "Inline table" else 0, int(match.group(2)))

    @property
    def table_label(self) -> str:
        match = re.match(r"((?:Inline table|Table) \d+)", self.address)
        return match.group(1) if match else "Table"


@dataclass
class DiffReport:
    comparisons: list[CellComparison]
    paper_source: dict = field(default_factory=dict)
    run: dict = field(default_factory=dict)
    unmatched_results: list[str] = field(default_factory=list)

    def counts(self) -> dict[Status, int]:
        out = dict.fromkeys(Status, 0)
        for comparison in self.comparisons:
            out[comparison.status] += 1
        return out

    @property
    def reproduced(self) -> int:
        return self.counts()[Status.GREEN]

    @property
    def comparable(self) -> int:
        counts = self.counts()
        return counts[Status.GREEN] + counts[Status.YELLOW] + counts[Status.RED]

    def by_table(self) -> dict[str, list[CellComparison]]:
        """Comparisons grouped by their table's label, in paper order."""
        grouped: dict[str, list[CellComparison]] = {}
        for comparison in sorted(self.comparisons, key=lambda c: c.table_key):
            grouped.setdefault(comparison.table_label, []).append(comparison)
        return grouped


def parse_tolerance(spec: str | None) -> Tolerance:
    """Parse `--tolerance`: a bare number is relative, or `rel=..,abs=..` pairs."""
    if not spec:
        return Tolerance()
    text = spec.strip()
    try:
        return Tolerance(relative=float(text))
    except ValueError:
        pass
    relative, absolute, multiplier = 0.05, None, 3.0
    for part in re.split(r"[,\s]+", text):
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"cannot parse tolerance fragment {part!r}; expected key=value")
        key, _, value = part.partition("=")
        key = key.strip().lower()
        try:
            number = float(value)
        except ValueError as exc:
            raise ValueError(f"tolerance {key} must be a number, got {value!r}") from exc
        if key in ("rel", "relative"):
            relative = number
        elif key in ("abs", "absolute"):
            absolute = number
        elif key in ("yellow", "yellow_multiplier"):
            multiplier = number
        else:
            raise ValueError(f"unknown tolerance key {key!r}")
    return Tolerance(relative=relative, absolute=absolute, yellow_multiplier=multiplier)


def _compare_values(cell: Cell, result: ResultCell, tolerance: Tolerance) -> CellComparison:
    paper_value = cell.value
    result_value = result.value
    assert paper_value is not None and result_value is not None

    delta = result_value - paper_value
    band = tolerance.band(paper_value)
    relative = abs(delta) / abs(paper_value) if paper_value else None
    notes: list[str] = []

    if cell.unit is not Unit.NONE:
        notes.append(f"paper reports this in {cell.unit.value}; values compared as given")

    # A result inside the paper's own reported uncertainty is a reproduction,
    # whatever the configured band says.
    if cell.uncertainty is not None and abs(delta) <= abs(cell.uncertainty):
        notes.append(f"within paper's reported ±{cell.uncertainty:g}")
        status = Status.GREEN
    elif abs(delta) <= band:
        status = Status.GREEN
    elif paper_value * result_value < 0:
        notes.append("sign differs from the paper")
        status = Status.RED
    elif abs(delta) <= band * tolerance.yellow_multiplier:
        status = Status.YELLOW
    else:
        status = Status.RED

    # A caveat the executor attached — an unseeded script, a derived column —
    # travels with the number rather than replacing it.
    notes.extend(result.caveats)

    return CellComparison(
        address=cell.address,
        status=status,
        paper_value=paper_value,
        result_value=result_value,
        paper_text=cell.text,
        delta=delta,
        relative_delta=relative,
        band=band,
        notes=notes,
    )


def compare(
    paper: Paper,
    results: Results,
    policy: TolerancePolicy | None = None,
) -> DiffReport:
    """Join a paper's numeric cells to results by address and grade each one."""
    policy = policy or TolerancePolicy()
    by_address = results.by_address()
    comparisons: list[CellComparison] = []
    matched: set[str] = set()

    for table in paper.tables:
        tolerance = policy.for_table(table.id)
        for cell in table.cells:
            if not cell.is_numeric:
                continue
            result = by_address.get(cell.address)
            if result is None:
                comparisons.append(
                    CellComparison(
                        address=cell.address,
                        status=Status.NOT_ATTEMPTED,
                        paper_value=cell.value,
                        result_value=None,
                        paper_text=cell.text,
                        notes=["no entry for this cell in the results file"],
                    )
                )
                continue
            matched.add(cell.address)

            if result.is_failure:
                code = parse_code(result.failure_code or "OTHER")
                validate(code, result.evidence)
                comparisons.append(
                    CellComparison(
                        address=cell.address,
                        status=Status.UNRUNNABLE,
                        paper_value=cell.value,
                        result_value=None,
                        paper_text=cell.text,
                        failure_code=code,
                        evidence=result.evidence,
                    )
                )
                continue

            if result.value is None:
                comparisons.append(
                    CellComparison(
                        address=cell.address,
                        status=Status.UNRUNNABLE,
                        paper_value=cell.value,
                        result_value=None,
                        paper_text=cell.text,
                        failure_code=FailureCode.OTHER,
                        evidence="results entry carried neither a value nor a failure code",
                    )
                )
                continue

            comparisons.append(_compare_values(cell, result, tolerance))

    unmatched = sorted(set(by_address) - matched)
    return DiffReport(
        comparisons=comparisons,
        paper_source=paper.source,
        run=results.run,
        unmatched_results=unmatched,
    )
