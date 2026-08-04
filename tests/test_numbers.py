from __future__ import annotations

import pytest

from recheck.paper.numbers import parse_cell
from recheck.schema import UncertaintyKind, Unit


class TestPlainValues:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("8.42", 8.42),
            ("-0.31", -0.31),
            ("+6.3", 6.3),
            ("1e-3", 1e-3),
            (r"$7.95$", 7.95),
            (r"\textbf{78.4}", 78.4),
            ("1,234", 1234.0),
        ],
    )
    def test_reads_value(self, raw: str, expected: float) -> None:
        parsed = parse_cell(raw)
        assert parsed.is_numeric
        assert parsed.value == pytest.approx(expected)


class TestUncertainty:
    def test_reads_pm(self) -> None:
        parsed = parse_cell(r"$1.42 \pm 0.21$")
        assert parsed.value == pytest.approx(1.42)
        assert parsed.uncertainty == pytest.approx(0.21)
        assert parsed.uncertainty_kind is UncertaintyKind.PM

    def test_reads_parenthesised_std(self) -> None:
        parsed = parse_cell("78.1 (0.4)")
        assert parsed.value == pytest.approx(78.1)
        assert parsed.uncertainty == pytest.approx(0.4)
        assert parsed.uncertainty_kind is UncertaintyKind.PAREN

    def test_reads_subscript(self) -> None:
        parsed = parse_cell("$1.42_{0.21}$")
        assert parsed.value == pytest.approx(1.42)
        assert parsed.uncertainty == pytest.approx(0.21)
        assert parsed.uncertainty_kind is UncertaintyKind.SUBSCRIPT

    def test_uncertainty_is_absolute(self) -> None:
        assert parse_cell(r"$1.42 \pm -0.21$").uncertainty == pytest.approx(0.21)


class TestUnitsAndMarkers:
    def test_percent(self) -> None:
        parsed = parse_cell(r"71.2\%")
        assert parsed.value == pytest.approx(71.2)
        assert parsed.unit is Unit.PERCENT

    def test_times(self) -> None:
        parsed = parse_cell(r"$1.5\times$")
        assert parsed.value == pytest.approx(1.5)
        assert parsed.unit is Unit.TIMES

    def test_significance_star_is_a_marker(self) -> None:
        parsed = parse_cell("1.42*")
        assert parsed.value == pytest.approx(1.42)
        assert "*" in parsed.markers

    def test_comparator_is_a_marker(self) -> None:
        parsed = parse_cell("<0.01")
        assert parsed.value == pytest.approx(0.01)
        assert "<" in parsed.markers


class TestNonNumeric:
    @pytest.mark.parametrize("raw", ["--", "-", "n/a", "N/A", "", "?", r"\textsc{n/a}"])
    def test_placeholders_are_not_numeric(self, raw: str) -> None:
        assert not parse_cell(raw).is_numeric

    @pytest.mark.parametrize("raw", ["GPT-2-large", "Pythia-1.4B", "+ finetune, 3 seeds"])
    def test_labels_containing_digits_are_not_numeric(self, raw: str) -> None:
        # The failure this guards: reading "GPT-2-large" as the number 2 would
        # silently corrupt every downstream comparison.
        assert not parse_cell(raw).is_numeric

    def test_text_is_preserved_for_non_numeric_cells(self) -> None:
        assert parse_cell(r"\textbf{GPT-2-large}").text == "GPT-2-large"


class TestScientificNotation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (r"$2.3\cdot10^{19}$", 2.3e19),
            (r"$1.0 \cdot 10^{20}$", 1.0e20),
            (r"$1.5\times10^{-3}$", 1.5e-3),
            (r"$9.6\cdot10^{18}$", 9.6e18),
        ],
    )
    def test_reads_mantissa_and_exponent(self, raw: str, expected: float) -> None:
        parsed = parse_cell(raw)
        assert parsed.is_numeric
        assert parsed.value == pytest.approx(expected)

    def test_bare_mantissa_would_be_wrong(self) -> None:
        # Guards the failure this exists to prevent: reading 2.3e19 as 2.3.
        assert parse_cell(r"$2.3\cdot10^{19}$").value > 1e18
