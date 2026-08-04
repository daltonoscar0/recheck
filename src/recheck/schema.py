"""Versioned data contracts shared by the paper and diff sides.

`paper.json` is produced by `recheck extract`; `results.json` is produced by the
execution agent (milestone 2) and hand-written by tests today. Both are public
contracts: bump SCHEMA_VERSION and document the change in docs/SCHEMA.md before
altering any field meaning.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"

ADDRESS_SEP = " › "  # single right-pointing angle quotation mark


class UncertaintyKind(StrEnum):
    """How a paper expressed a cell's uncertainty, when it did."""

    NONE = "none"
    PM = "pm"  # 1.42 \pm 0.21
    PAREN = "paren"  # 1.42 (0.21)
    SUBSCRIPT = "subscript"  # 1.42_{0.21}


class Unit(StrEnum):
    NONE = "none"
    PERCENT = "percent"
    TIMES = "times"


@dataclass
class Cell:
    """One extracted table cell, numeric or not.

    `address` is the stable identity used to join paper cells to results cells.
    It is human-readable by design so a results file can be hand-written.
    """

    address: str
    row_path: list[str]
    col_path: list[str]
    row: int
    col: int
    raw: str
    text: str
    is_numeric: bool
    value: float | None = None
    uncertainty: float | None = None
    uncertainty_kind: UncertaintyKind = UncertaintyKind.NONE
    unit: Unit = Unit.NONE
    emphasis: list[str] = field(default_factory=list)
    markers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "row_path": self.row_path,
            "col_path": self.col_path,
            "row": self.row,
            "col": self.col,
            "raw": self.raw,
            "text": self.text,
            "is_numeric": self.is_numeric,
            "value": self.value,
            "uncertainty": self.uncertainty,
            "uncertainty_kind": self.uncertainty_kind.value,
            "unit": self.unit.value,
            "emphasis": self.emphasis,
            "markers": self.markers,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Cell:
        return cls(
            address=d["address"],
            row_path=list(d.get("row_path", [])),
            col_path=list(d.get("col_path", [])),
            row=d["row"],
            col=d["col"],
            raw=d.get("raw", ""),
            text=d.get("text", ""),
            is_numeric=d.get("is_numeric", False),
            value=d.get("value"),
            uncertainty=d.get("uncertainty"),
            uncertainty_kind=UncertaintyKind(d.get("uncertainty_kind", "none")),
            unit=Unit(d.get("unit", "none")),
            emphasis=list(d.get("emphasis", [])),
            markers=list(d.get("markers", [])),
        )


@dataclass
class Table:
    index: int
    label: str | None
    caption: str
    environment: str
    column_headers: list[list[str]]
    cells: list[Cell]
    referencing_paragraphs: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.label or f"table-{self.index}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "index": self.index,
            "label": self.label,
            "caption": self.caption,
            "environment": self.environment,
            "column_headers": self.column_headers,
            "cells": [c.to_dict() for c in self.cells],
            "context": {"referencing_paragraphs": self.referencing_paragraphs},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Table:
        return cls(
            index=d["index"],
            label=d.get("label"),
            caption=d.get("caption", ""),
            environment=d.get("environment", "tabular"),
            column_headers=[list(r) for r in d.get("column_headers", [])],
            cells=[Cell.from_dict(c) for c in d.get("cells", [])],
            referencing_paragraphs=list(d.get("context", {}).get("referencing_paragraphs", [])),
        )


@dataclass
class Paper:
    tables: list[Table]
    source: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "tables": [t.to_dict() for t in self.tables],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Paper:
        _require_version(d.get("schema_version"), "paper.json")
        return cls(
            tables=[Table.from_dict(t) for t in d.get("tables", [])],
            source=d.get("source", {}),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n")

    @classmethod
    def read(cls, path: Path) -> Paper:
        return cls.from_dict(json.loads(path.read_text()))

    def cell_by_address(self, address: str) -> Cell | None:
        for table in self.tables:
            for cell in table.cells:
                if cell.address == address:
                    return cell
        return None


@dataclass
class ResultCell:
    """One freshly-produced number, or one honest reason there isn't a number."""

    address: str
    value: float | None = None
    uncertainty: float | None = None
    n_seeds: int | None = None
    failure_code: str | None = None
    evidence: str | None = None

    @property
    def is_failure(self) -> bool:
        return self.failure_code is not None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"address": self.address}
        if self.is_failure:
            out["failure"] = {"code": self.failure_code, "evidence": self.evidence}
        else:
            out["value"] = self.value
            if self.uncertainty is not None:
                out["uncertainty"] = self.uncertainty
            if self.n_seeds is not None:
                out["n_seeds"] = self.n_seeds
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ResultCell:
        failure = d.get("failure") or {}
        return cls(
            address=d["address"],
            value=d.get("value"),
            uncertainty=d.get("uncertainty"),
            n_seeds=d.get("n_seeds"),
            failure_code=failure.get("code"),
            evidence=failure.get("evidence"),
        )


@dataclass
class Results:
    cells: list[ResultCell]
    run: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run": self.run,
            "cells": [c.to_dict() for c in self.cells],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Results:
        _require_version(d.get("schema_version"), "results.json")
        return cls(
            cells=[ResultCell.from_dict(c) for c in d.get("cells", [])],
            run=d.get("run", {}),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )

    @classmethod
    def read(cls, path: Path) -> Results:
        return cls.from_dict(json.loads(path.read_text()))

    def by_address(self) -> dict[str, ResultCell]:
        return {c.address: c for c in self.cells}


class SchemaError(ValueError):
    """Raised when an input file declares a schema version we cannot read."""


def _require_version(version: str | None, what: str) -> None:
    if version is None:
        raise SchemaError(f"{what} is missing a schema_version field")
    major = version.split(".", 1)[0]
    if major != SCHEMA_VERSION.split(".", 1)[0]:
        raise SchemaError(
            f"{what} declares schema_version {version}, which this build of recheck "
            f"(schema {SCHEMA_VERSION}) cannot read"
        )


def make_address(table_index: int, row_path: list[str], col_path: list[str]) -> str:
    parts = [f"Table {table_index}", *(p for p in row_path if p), *(p for p in col_path if p)]
    return ADDRESS_SEP.join(parts)
