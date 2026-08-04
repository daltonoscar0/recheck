"""The mapper's output: how each paper cell could be produced from the repo.

A plan is deliberately readable JSON. It is the artifact a reviewer looks at
when they disagree with a report — "you aggregated the wrong column" is a much
more useful complaint than "the number is wrong", and it is only possible to
make if the recipe is written down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..diff.taxonomy import FailureCode


class Statistic(StrEnum):
    MEAN = "mean"
    MEDIAN = "median"
    SUM = "sum"
    COUNT = "count"
    STDEV = "stdev"


@dataclass(frozen=True)
class Filter:
    column: str
    value: str

    def to_dict(self) -> dict[str, str]:
        return {"column": self.column, "value": self.value}

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> Filter:
        return cls(column=d["column"], value=d["value"])

    def __str__(self) -> str:
        return f"{self.column}={self.value}"


@dataclass(frozen=True)
class AggregateRecipe:
    """Read one number out of a tabular artifact."""

    artifact: str
    value_column: str
    statistic: Statistic = Statistic.MEAN
    filters: tuple[Filter, ...] = ()
    uncertainty: Statistic | None = None

    kind = "aggregate"

    def describe(self) -> str:
        where = " where " + ", ".join(str(f) for f in self.filters) if self.filters else ""
        return f"{self.statistic.value}({self.value_column}) over {self.artifact}{where}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "artifact": self.artifact,
            "value_column": self.value_column,
            "statistic": self.statistic.value,
            "filters": [f.to_dict() for f in self.filters],
            "uncertainty": self.uncertainty.value if self.uncertainty else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AggregateRecipe:
        raw_uncertainty = d.get("uncertainty")
        return cls(
            artifact=d["artifact"],
            value_column=d["value_column"],
            statistic=Statistic(d.get("statistic", "mean")),
            filters=tuple(Filter.from_dict(f) for f in d.get("filters", [])),
            uncertainty=Statistic(raw_uncertainty) if raw_uncertainty else None,
        )


@dataclass(frozen=True)
class DifferenceRecipe:
    """A column that is the difference of two others in the same row.

    Papers routinely print an effect-size column that no script writes out —
    it is arithmetic over two columns the reader can already see. Deriving it
    is safe only because the derivation is written down here and repeated in
    the report, so nobody mistakes it for a measured quantity.
    """

    minuend: str  # address of the left operand
    subtrahend: str  # address of the right operand

    kind = "difference"

    def describe(self) -> str:
        return f"({self.minuend}) − ({self.subtrahend})"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "minuend": self.minuend, "subtrahend": self.subtrahend}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DifferenceRecipe:
        return cls(minuend=d["minuend"], subtrahend=d["subtrahend"])


Recipe = AggregateRecipe | DifferenceRecipe


def recipe_from_dict(d: dict[str, Any]) -> Recipe:
    if d.get("kind") == DifferenceRecipe.kind:
        return DifferenceRecipe.from_dict(d)
    return AggregateRecipe.from_dict(d)


@dataclass(frozen=True)
class PlanFailure:
    """A cell the mapper could not route, with the reason a reader needs."""

    code: FailureCode
    evidence: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "evidence": self.evidence}

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> PlanFailure:
        return cls(code=FailureCode(d["code"]), evidence=d["evidence"])


@dataclass(frozen=True)
class EntryPoint:
    """A script that plausibly produces a table, with why we think so."""

    script: str
    argv: tuple[str, ...]
    score: float
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "script": self.script,
            "argv": list(self.argv),
            "score": self.score,
            "why": self.why,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EntryPoint:
        return cls(
            script=d["script"],
            argv=tuple(d.get("argv", [])),
            score=float(d.get("score", 0.0)),
            why=d.get("why", ""),
        )


@dataclass
class CellPlan:
    address: str
    recipe: Recipe | None = None
    failure: PlanFailure | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"address": self.address}
        if self.recipe is not None:
            out["recipe"] = self.recipe.to_dict()
        if self.failure is not None:
            out["failure"] = self.failure.to_dict()
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CellPlan:
        return cls(
            address=d["address"],
            recipe=recipe_from_dict(d["recipe"]) if d.get("recipe") else None,
            failure=PlanFailure.from_dict(d["failure"]) if d.get("failure") else None,
        )


@dataclass
class TablePlan:
    table_id: str
    table_index: int
    cells: list[CellPlan] = field(default_factory=list)
    candidates: list[EntryPoint] = field(default_factory=list)
    considered: list[str] = field(default_factory=list)
    """Every script the mapper weighed, so `SCRIPT_NOT_FOUND` can name them."""

    def artifacts(self) -> list[str]:
        seen: list[str] = []
        for cell in self.cells:
            if isinstance(cell.recipe, AggregateRecipe) and cell.recipe.artifact not in seen:
                seen.append(cell.recipe.artifact)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_id": self.table_id,
            "table_index": self.table_index,
            "cells": [c.to_dict() for c in self.cells],
            "candidates": [c.to_dict() for c in self.candidates],
            "considered": self.considered,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TablePlan:
        return cls(
            table_id=d["table_id"],
            table_index=d["table_index"],
            cells=[CellPlan.from_dict(c) for c in d.get("cells", [])],
            candidates=[EntryPoint.from_dict(c) for c in d.get("candidates", [])],
            considered=list(d.get("considered", [])),
        )


PLAN_VERSION = "1"


@dataclass
class RepoPlan:
    commit: str
    mapper: str
    tables: list[TablePlan] = field(default_factory=list)
    plan_version: str = PLAN_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_version": self.plan_version,
            "commit": self.commit,
            "mapper": self.mapper,
            "tables": [t.to_dict() for t in self.tables],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RepoPlan:
        return cls(
            commit=d["commit"],
            mapper=d.get("mapper", "unknown"),
            tables=[TablePlan.from_dict(t) for t in d.get("tables", [])],
            plan_version=d.get("plan_version", PLAN_VERSION),
        )
