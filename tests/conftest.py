from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"
RESULTS = FIXTURES / "results"

FIXTURE_NAMES = [
    "clean_booktabs",
    "multilevel_uncertainty",
    "messy_labels",
    "garden_path_calibration",
    "demo_report",
]


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def golden_dir() -> Path:
    return GOLDEN


@pytest.fixture(scope="session")
def results_dir() -> Path:
    return RESULTS


def read_fixture(name: str) -> str:
    return (FIXTURES / f"{name}.tex").read_text()


def read_golden(name: str) -> dict:
    return json.loads((GOLDEN / f"{name}.json").read_text())


def cell_at(paper, address: str):
    """Fetch a cell by address, failing loudly when extraction did not produce it."""
    found = paper.cell_by_address(address)
    assert found is not None, f"no cell extracted at address {address!r}"
    return found
