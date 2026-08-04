from __future__ import annotations

import json

import pytest

from recheck.schema import Paper, Results, SchemaError, make_address


class TestRoundTrip:
    def test_paper_survives_a_round_trip(self, fixtures_dir) -> None:
        from recheck.paper import extract_tables

        original = extract_tables((fixtures_dir / "multilevel_uncertainty.tex").read_text())
        restored = Paper.from_dict(json.loads(json.dumps(original.to_dict())))
        assert restored.to_dict() == original.to_dict()

    def test_results_survive_a_round_trip(self, results_dir) -> None:
        original = Results.read(results_dir / "demo_results.json")
        restored = Results.from_dict(json.loads(json.dumps(original.to_dict())))
        assert restored.to_dict() == original.to_dict()

    def test_paper_write_and_read(self, tmp_path, fixtures_dir) -> None:
        from recheck.paper import extract_tables

        paper = extract_tables((fixtures_dir / "clean_booktabs.tex").read_text())
        path = tmp_path / "paper.json"
        paper.write(path)
        assert Paper.read(path).to_dict() == paper.to_dict()


class TestVersioning:
    def test_missing_version_is_rejected(self) -> None:
        with pytest.raises(SchemaError, match="missing a schema_version"):
            Paper.from_dict({"tables": []})

    def test_future_major_version_is_rejected(self) -> None:
        with pytest.raises(SchemaError, match="cannot read"):
            Results.from_dict({"schema_version": "2.0", "cells": []})

    def test_compatible_minor_version_is_accepted(self) -> None:
        assert Results.from_dict({"schema_version": "1.7", "cells": []}).cells == []


class TestAddresses:
    def test_joins_paths(self) -> None:
        assert make_address(2, ["Pythia-1.4B"], ["NP/Z", "delta"]) == (
            "Table 2 › Pythia-1.4B › NP/Z › delta"
        )

    def test_drops_empty_segments(self) -> None:
        assert make_address(1, ["", "A"], ["", "B"]) == "Table 1 › A › B"


class TestResultCells:
    def test_failure_cells_omit_value_keys(self, results_dir) -> None:
        results = Results.read(results_dir / "demo_results.json")
        failures = [c for c in results.cells if c.is_failure]
        assert failures
        for cell in failures:
            assert "value" not in cell.to_dict()
            assert cell.to_dict()["failure"]["evidence"]

    def test_by_address_indexes_every_cell(self, results_dir) -> None:
        results = Results.read(results_dir / "demo_results.json")
        assert len(results.by_address()) == len(results.cells)
