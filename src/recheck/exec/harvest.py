"""Turn a recipe plus a file on disk into one number.

This is the only place a value enters a report, so it is deliberately dull:
filter rows, take a column, apply one statistic. Anything it cannot do — an
empty selection, a non-numeric column, a missing file — is an error carrying
the recipe that produced it, because "the number is wrong" is unactionable and
"you took the mean of 0 rows where condition=ambigous" is a bug report.
"""

from __future__ import annotations

import csv
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

from ..map.plan import AggregateRecipe, Statistic

#: Values are rounded so a results file stays readable and hand-checkable.
#: Three decimals is finer than any tolerance band the diff engine applies.
VALUE_PRECISION = 3


class HarvestError(RuntimeError):
    """Raised when a recipe cannot be applied to what is on disk."""


@dataclass(frozen=True)
class Harvested:
    value: float
    uncertainty: float | None = None
    n_rows: int = 0


def _delimiter(path: Path) -> str:
    return "\t" if path.suffix.lower() == ".tsv" else ","


def _matches(row: dict[str, str], recipe: AggregateRecipe) -> bool:
    return all((row.get(f.column) or "").strip() == f.value for f in recipe.filters)


def _apply(statistic: Statistic, values: list[float]) -> float:
    if statistic is Statistic.MEAN:
        return statistics.mean(values)
    if statistic is Statistic.MEDIAN:
        return statistics.median(values)
    if statistic is Statistic.SUM:
        return sum(values)
    if statistic is Statistic.COUNT:
        return float(len(values))
    if len(values) < 2:
        raise HarvestError(f"{statistic.value} needs at least two values, got {len(values)}")
    return statistics.stdev(values)


def aggregate(root: Path, recipe: AggregateRecipe) -> Harvested:
    """Read one number out of the artifact this recipe names."""
    path = root / recipe.artifact
    if not path.is_file():
        raise HarvestError(f"{recipe.artifact} does not exist under {root}")

    values: list[float] = []
    skipped = 0
    try:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle, delimiter=_delimiter(path))
            columns = reader.fieldnames or []
            if recipe.value_column not in columns:
                raise HarvestError(
                    f"{recipe.artifact} has no column {recipe.value_column!r} "
                    f"(it has {', '.join(columns) or 'no columns'})"
                )
            for filter_ in recipe.filters:
                if filter_.column not in columns:
                    raise HarvestError(
                        f"{recipe.artifact} has no column {filter_.column!r} to filter on"
                    )
            for row in reader:
                if not _matches(row, recipe):
                    continue
                raw = (row.get(recipe.value_column) or "").strip()
                try:
                    parsed = float(raw)
                except ValueError:
                    skipped += 1
                    continue
                # float() happily accepts 'nan' and 'inf'. One NaN row poisons a
                # mean and a stdev into NaN, which then reaches the report as a
                # number. A value that is not finite is not a measurement.
                if not math.isfinite(parsed):
                    skipped += 1
                    continue
                values.append(parsed)
    except (OSError, csv.Error) as exc:
        raise HarvestError(f"could not read {recipe.artifact}: {exc}") from exc

    if not values:
        where = ", ".join(str(f) for f in recipe.filters) or "no filters"
        raise HarvestError(
            f"{recipe.describe()} selected 0 numeric rows from {recipe.artifact} "
            f"({where}; {skipped} non-numeric rows skipped)"
        )

    uncertainty = None
    if recipe.uncertainty is not None and len(values) > 1:
        uncertainty = round(_apply(recipe.uncertainty, values), VALUE_PRECISION)
    return Harvested(
        value=round(_apply(recipe.statistic, values), VALUE_PRECISION),
        uncertainty=uncertainty,
        n_rows=len(values),
    )


def difference(minuend: float, subtrahend: float) -> float:
    return round(minuend - subtrahend, VALUE_PRECISION)
