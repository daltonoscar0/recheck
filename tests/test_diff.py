from __future__ import annotations

import pytest

from recheck.diff import FailureCode, Status, TaxonomyError, Tolerance, TolerancePolicy, compare
from recheck.diff.engine import default_absolute, parse_tolerance
from recheck.diff.render import render_markdown
from recheck.diff.taxonomy import validate
from recheck.schema import Cell, Paper, ResultCell, Results, Table, UncertaintyKind


def make_paper(value: float, uncertainty: float | None = None) -> Paper:
    cell = Cell(
        address="Table 1 › Model › Metric",
        row_path=["Model"],
        col_path=["Metric"],
        row=1,
        col=1,
        raw=str(value),
        text=str(value),
        is_numeric=True,
        value=value,
        uncertainty=uncertainty,
        uncertainty_kind=UncertaintyKind.PM if uncertainty else UncertaintyKind.NONE,
    )
    table = Table(
        index=1, label="tab:t", caption="", environment="tabular",
        column_headers=[["Model"], ["Metric"]], cells=[cell],
    )
    return Paper(tables=[table])


def make_results(**kwargs) -> Results:
    return Results(cells=[ResultCell(address="Table 1 › Model › Metric", **kwargs)])


def status_of(paper: Paper, results: Results, policy: TolerancePolicy | None = None) -> Status:
    return compare(paper, results, policy).comparisons[0].status


class TestStatusBands:
    def test_exact_match_is_green(self) -> None:
        assert status_of(make_paper(1.0), make_results(value=1.0)) is Status.GREEN

    def test_within_band_is_green(self) -> None:
        assert status_of(make_paper(10.0), make_results(value=10.4)) is Status.GREEN

    def test_just_outside_band_is_yellow(self) -> None:
        # band = max(0.05, 10 * 0.05) = 0.5; yellow reaches 1.5
        assert status_of(make_paper(10.0), make_results(value=11.0)) is Status.YELLOW

    def test_far_outside_band_is_red(self) -> None:
        assert status_of(make_paper(10.0), make_results(value=25.0)) is Status.RED

    def test_sign_flip_is_red_even_when_close(self) -> None:
        # Magnitude alone would call this yellow; direction is what a reader cares about.
        assert status_of(make_paper(0.05), make_results(value=-0.05)) is Status.RED

    def test_tighter_tolerance_downgrades(self) -> None:
        policy = TolerancePolicy(default=Tolerance(relative=0.001, absolute=0.001))
        assert status_of(make_paper(10.0), make_results(value=10.4), policy) is Status.RED


class TestReportedUncertainty:
    def test_within_reported_std_is_green_despite_band(self) -> None:
        # 1.42 ± 0.21 vs 1.60: outside a 5% band, inside the paper's own spread.
        assert status_of(make_paper(1.42, 0.21), make_results(value=1.60)) is Status.GREEN

    def test_note_explains_the_override(self) -> None:
        report = compare(make_paper(1.42, 0.21), make_results(value=1.60))
        assert "within paper's reported ±0.21" in report.comparisons[0].notes

    def test_outside_reported_std_still_graded_by_band(self) -> None:
        assert status_of(make_paper(1.42, 0.05), make_results(value=3.0)) is Status.RED


class TestFailures:
    def test_failure_code_yields_unrunnable(self) -> None:
        results = make_results(failure_code="GATED_DATASET", evidence="needs a licence")
        comparison = compare(make_paper(1.0), results).comparisons[0]
        assert comparison.status is Status.UNRUNNABLE
        assert comparison.failure_code is FailureCode.GATED_DATASET
        assert comparison.evidence == "needs a licence"

    def test_unknown_code_is_rejected(self) -> None:
        results = make_results(failure_code="WHOOPS", evidence="x")
        with pytest.raises(TaxonomyError, match="unknown failure code"):
            compare(make_paper(1.0), results)

    def test_other_requires_evidence(self) -> None:
        with pytest.raises(TaxonomyError, match="requires non-empty evidence"):
            validate(FailureCode.OTHER, "")

    def test_other_accepts_evidence(self) -> None:
        validate(FailureCode.OTHER, "the training script segfaults on line 41")

    def test_entry_with_neither_value_nor_failure_is_unrunnable(self) -> None:
        comparison = compare(make_paper(1.0), make_results()).comparisons[0]
        assert comparison.status is Status.UNRUNNABLE
        assert comparison.failure_code is FailureCode.OTHER


class TestCoverage:
    def test_missing_result_is_not_attempted(self) -> None:
        report = compare(make_paper(1.0), Results(cells=[]))
        assert report.comparisons[0].status is Status.NOT_ATTEMPTED

    def test_non_numeric_cells_are_skipped(self) -> None:
        paper = make_paper(1.0)
        paper.tables[0].cells[0].is_numeric = False
        assert compare(paper, Results(cells=[])).comparisons == []

    def test_unmatched_results_are_reported(self) -> None:
        results = Results(cells=[ResultCell(address="Table 9 › Ghost › Metric", value=1.0)])
        report = compare(make_paper(1.0), results)
        assert report.unmatched_results == ["Table 9 › Ghost › Metric"]

    def test_comparable_excludes_unrunnable(self) -> None:
        report = compare(make_paper(1.0), make_results(failure_code="BUDGET_EXCEEDED"))
        assert report.comparable == 0
        assert report.reproduced == 0


class TestToleranceParsing:
    def test_bare_number_is_relative(self) -> None:
        assert parse_tolerance("0.02").relative == pytest.approx(0.02)

    def test_key_value_pairs(self) -> None:
        tolerance = parse_tolerance("rel=0.01,abs=0.5,yellow=2")
        assert tolerance.relative == pytest.approx(0.01)
        assert tolerance.absolute == pytest.approx(0.5)
        assert tolerance.yellow_multiplier == pytest.approx(2.0)

    def test_none_is_the_default(self) -> None:
        assert parse_tolerance(None) == Tolerance()

    def test_unknown_key_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown tolerance key"):
            parse_tolerance("wobble=3")

    def test_non_numeric_value_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be a number"):
            parse_tolerance("rel=loose")

    @pytest.mark.parametrize(
        ("magnitude", "expected"),
        [(0.5, 0.01), (5.0, 0.05), (50.0, 0.5), (500.0, 1.0)],
    )
    def test_absolute_default_scales_with_magnitude(self, magnitude, expected) -> None:
        assert default_absolute(magnitude) == pytest.approx(expected)


class TestPerTablePolicy:
    def test_override_applies_to_named_table(self) -> None:
        policy = TolerancePolicy(
            default=Tolerance(relative=0.5),
            per_table={"tab:t": Tolerance(relative=0.0001, absolute=0.0001)},
        )
        assert status_of(make_paper(10.0), make_results(value=11.0), policy) is Status.RED

    def test_other_tables_keep_the_default(self) -> None:
        policy = TolerancePolicy(
            default=Tolerance(relative=0.5),
            per_table={"tab:other": Tolerance(relative=0.0001)},
        )
        assert status_of(make_paper(10.0), make_results(value=11.0), policy) is Status.GREEN


class TestMarkdownRendering:
    def test_includes_every_status_present(self) -> None:
        report = compare(make_paper(1.0), make_results(value=1.0))
        markdown = render_markdown(report)
        assert "# Reproduction report" in markdown
        assert "🟢 green" in markdown

    def test_failure_section_lists_evidence(self) -> None:
        results = make_results(failure_code="MISSING_CHECKPOINT", evidence="weights unpublished")
        markdown = render_markdown(compare(make_paper(1.0), results))
        assert "Why cells could not be run" in markdown
        assert "weights unpublished" in markdown

    def test_pipes_in_evidence_do_not_break_the_table(self) -> None:
        results = make_results(failure_code="OTHER", evidence="ran `a | b` and it failed")
        markdown = render_markdown(compare(make_paper(1.0), results))
        cell_rows = [
            line
            for line in markdown.splitlines()
            if line.startswith("|") and "Model › Metric" in line
        ]
        assert cell_rows
        for row in cell_rows:
            # Six columns means seven unescaped delimiters; the evidence pipe
            # must be escaped rather than opening a seventh column.
            assert row.count("|") - row.count("\\|") == 7
            assert "\\|" in row


class TestStatusMarks:
    def test_every_status_has_a_distinct_shape(self) -> None:
        # Colour is reinforcement, not the signal: stripped of ANSI the report
        # still has to be readable.
        from recheck.diff.render import STATUS_MARK

        marks = [STATUS_MARK[status] for status in Status]
        assert len(set(marks)) == len(marks)

    def test_terminal_report_distinguishes_green_from_red_without_colour(self) -> None:
        import re

        from rich.console import Console

        from recheck.diff.render import render_terminal

        green = compare(make_paper(1.0), make_results(value=1.0))
        red = compare(make_paper(1.0), make_results(value=99.0))
        rendered = []
        for report in (green, red):
            console = Console(file=__import__("io").StringIO(), width=100, no_color=True)
            render_terminal(report, console)
            rendered.append(re.sub(r"\x1b\[[0-9;]*m", "", console.file.getvalue()))
        assert rendered[0] != rendered[1]


class TestInferredEnvironmentIsSurfaced:
    def _report(self):
        report = compare(make_paper(1.0), make_results(value=1.0))
        report.run = {"environment": {"inferred": ["pandas", "scipy"]}}
        return report

    def test_markdown_states_it_in_the_header(self) -> None:
        from recheck.diff.render import render_markdown

        markdown = render_markdown(self._report())
        head = markdown.split("| Status |")[0]
        assert "inferred, not declared" in head
        assert "pandas" in head

    def test_terminal_states_it_too(self) -> None:
        import io
        import re

        from rich.console import Console

        from recheck.diff.render import render_terminal

        console = Console(file=io.StringIO(), width=120, no_color=True)
        render_terminal(self._report(), console)
        plain = re.sub(r"\x1b\[[0-9;]*m", "", console.file.getvalue())
        assert "inferred, not declared" in plain

    def test_a_declared_environment_adds_no_noise(self) -> None:
        from recheck.diff.render import render_markdown

        report = compare(make_paper(1.0), make_results(value=1.0))
        report.run = {"environment": {"inferred": []}}
        assert "inferred" not in render_markdown(report)


class TestNonFiniteValues:
    """NaN loses every comparison, so it must never be graded by fallthrough."""

    def test_a_nan_result_is_red_not_silently_graded(self) -> None:
        assert status_of(make_paper(1.0), make_results(value=float("nan"))) is Status.RED

    def test_the_reason_says_it_cannot_be_compared(self) -> None:
        report = compare(make_paper(1.0), make_results(value=float("nan")))
        assert "not finite" in " ".join(report.comparisons[0].notes)

    def test_infinity_is_handled_too(self) -> None:
        assert status_of(make_paper(1.0), make_results(value=float("inf"))) is Status.RED

    def test_a_non_finite_paper_value_is_caught(self) -> None:
        assert status_of(make_paper(float("nan")), make_results(value=1.0)) is Status.RED
