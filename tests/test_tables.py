from __future__ import annotations

import json

import pytest

from conftest import FIXTURE_NAMES, GOLDEN, cell_at, read_fixture, read_golden
from recheck.paper import extract_tables, find_main_tex, resolve_inputs
from recheck.schema import UncertaintyKind


@pytest.fixture(scope="module")
def clean():
    return extract_tables(read_fixture("clean_booktabs"))


@pytest.fixture(scope="module")
def multilevel():
    return extract_tables(read_fixture("multilevel_uncertainty"))


@pytest.fixture(scope="module")
def messy():
    return extract_tables(read_fixture("messy_labels"))


class TestCleanBooktabs:
    def test_finds_one_table(self, clean) -> None:
        assert len(clean.tables) == 1

    def test_reads_caption_and_label(self, clean) -> None:
        table = clean.tables[0]
        assert table.label == "tab:surprisal"
        assert table.caption.startswith("Mean surprisal in bits")

    def test_addresses_are_human_readable(self, clean) -> None:
        addresses = {c.address for c in clean.tables[0].cells}
        assert "Table 1 › GPT-2-large › Ambiguous" in addresses
        assert "Table 1 › Pythia-1.4B › Unambiguous" in addresses

    def test_reads_values(self, clean) -> None:
        cell = cell_at(clean, "Table 1 › GPT-2-large › Ambiguous")
        assert cell.value == pytest.approx(8.42)

    def test_stub_column_is_not_emitted_as_data(self, clean) -> None:
        assert all(c.col >= 1 for c in clean.tables[0].cells)

    def test_captures_referencing_paragraph(self, clean) -> None:
        context = clean.tables[0].referencing_paragraphs
        assert context and "disambiguating word" in context[0]

    def test_reference_is_resolved_to_a_number(self, clean) -> None:
        assert "tab:surprisal" not in " ".join(clean.tables[0].referencing_paragraphs)


class TestMultilevelHeaders:
    def test_column_path_spans_two_header_rows(self, multilevel) -> None:
        assert multilevel.tables[0].column_headers[2] == ["Surprisal Δ", "NP/Z"]

    def test_multirow_label_carries_down(self, multilevel) -> None:
        addresses = {c.address for c in multilevel.tables[0].cells}
        assert "Table 1 › GPT-2-large › Unambiguous › Surprisal Δ › NP/Z" in addresses

    def test_two_stub_columns_detected(self, multilevel) -> None:
        cell = cell_at(multilevel, "Table 1 › GPT-2-large › Ambiguous › Surprisal Δ › NP/Z")
        assert cell.row_path == ["GPT-2-large", "Ambiguous"]

    def test_reads_pm_uncertainty(self, multilevel) -> None:
        cell = cell_at(multilevel, "Table 1 › Pythia-1.4B › Ambiguous › Surprisal Δ › NP/S")
        assert cell.value == pytest.approx(0.79)
        assert cell.uncertainty == pytest.approx(0.22)
        assert cell.uncertainty_kind is UncertaintyKind.PM

    def test_records_bold_without_interpreting_it(self, multilevel) -> None:
        cell = cell_at(multilevel, "Table 1 › GPT-2-large › Unambiguous › Surprisal Δ › NP/Z")
        assert cell.emphasis == ["bold"]
        assert cell.value == pytest.approx(0.31)

    def test_cmidrule_does_not_leak_into_headers(self, multilevel) -> None:
        joined = json.dumps(multilevel.to_dict())
        assert "3-4" not in joined

    def test_cell_count(self, multilevel) -> None:
        assert len(multilevel.tables[0].cells) == 8


class TestMessyLabels:
    def test_finds_both_tables(self, messy) -> None:
        assert len(messy.tables) == 2

    def test_label_inside_caption_is_read(self, messy) -> None:
        assert messy.tables[0].label == "tab:probe"

    def test_label_does_not_leak_into_caption(self, messy) -> None:
        assert "tab:probe" not in messy.tables[0].caption

    def test_unlabelled_table_gets_synthetic_id(self, messy) -> None:
        assert messy.tables[1].label is None
        assert messy.tables[1].id == "table-2"

    def test_hline_separates_header_from_body(self, messy) -> None:
        assert messy.tables[0].column_headers[1] == ["Dev"]

    def test_placeholder_cell_is_non_numeric(self, messy) -> None:
        cell = cell_at(messy, "Table 1 › Baseline › Δ")
        assert not cell.is_numeric
        assert cell.value is None

    def test_section_row_joins_the_row_path(self, messy) -> None:
        cell = cell_at(messy, "Table 2 › Ambiguous › NP/Z › GPT-2-large")
        assert cell.row_path == ["Ambiguous", "NP/Z"]
        assert cell.value == pytest.approx(1.42)

    def test_section_rows_are_not_emitted_as_cells(self, messy) -> None:
        texts = {c.text for c in messy.tables[1].cells}
        assert "Ambiguous" not in texts


class TestMultiFileProjects:
    def test_resolves_input(self) -> None:
        main = r"\documentclass{article}\begin{document}\input{tables/main}\end{document}"
        files = {"tables/main": r"\begin{tabular}{lc}A & 1 \\\end{tabular}"}
        resolved = resolve_inputs(main, files.get)
        assert "tabular" in resolved

    def test_missing_input_is_dropped_not_fatal(self) -> None:
        main = r"\input{absent}\begin{tabular}{lc}A & 1 \\\end{tabular}"
        resolved = resolve_inputs(main, lambda name: None)
        assert r"\input" not in resolved
        assert "tabular" in resolved

    def test_finds_main_file_by_documentclass(self) -> None:
        files = {"sections/intro.tex": "Some prose.", "paper.tex": r"\documentclass{article}"}
        assert find_main_tex(files) == "paper.tex"

    def test_returns_none_without_documentclass(self) -> None:
        assert find_main_tex({"a.tex": "prose"}) is None


class TestRobustness:
    def test_document_without_tables_yields_none(self) -> None:
        assert extract_tables(r"\documentclass{article} No tables here.").tables == []

    def test_table_float_without_tabular_is_skipped(self) -> None:
        doc = r"\begin{table}\caption{just a caption}\end{table}"
        assert extract_tables(doc).tables == []

    def test_commented_out_table_is_ignored(self) -> None:
        doc = "% \\begin{tabular}{lc}A & 1 \\\\ \\end{tabular}"
        assert extract_tables(doc).tables == []

    def test_duplicate_addresses_are_disambiguated(self) -> None:
        doc = r"""
        \begin{tabular}{lcc}
        Model & Score & Score \\
        \midrule
        A & 1.0 & 2.0 \\
        \end{tabular}
        """
        cells = extract_tables(doc).tables[0].cells
        assert len({c.address for c in cells}) == len(cells)


class TestGoldenFiles:
    @pytest.mark.parametrize("name", FIXTURE_NAMES)
    def test_extraction_matches_golden(self, name: str) -> None:
        produced = extract_tables(read_fixture(name)).to_dict()
        expected = read_golden(name)
        assert produced == expected, (
            f"extraction of {name}.tex changed. If the change is intended, "
            f"regenerate with: python scripts/update_golden.py"
        )

    def test_every_fixture_has_a_golden_file(self) -> None:
        on_disk = {p.stem for p in GOLDEN.glob("*.json")}
        assert on_disk == set(FIXTURE_NAMES)


class TestLayoutTabularsAreNotTables:
    """A tabular with no data cells is layout, and numbering must skip it."""

    LAYOUT = r"""
    \begin{table}
    \begin{tabular}{l}
    Excerpt of labeling instructions \\
    \end{tabular}
    \end{table}

    \begin{table}
    \caption{Real results}
    \label{tab:real}
    \begin{tabular}{lc}
    Model & Score \\
    \midrule
    A & 1.0 \\
    \end{tabular}
    \end{table}
    """

    def test_an_empty_tabular_is_dropped(self) -> None:
        assert [t.id for t in extract_tables(self.LAYOUT).tables] == ["tab:real"]

    def test_numbering_does_not_skip(self) -> None:
        # The layout float must not consume "Table 1" and push the real table
        # to "Table 2" — the address is what a results file joins against.
        paper = extract_tables(self.LAYOUT)
        assert paper.tables[0].index == 1
        assert all(c.address.startswith("Table 1 ") for c in paper.tables[0].cells)


class TestMultiLineHeaderCells:
    def test_a_nested_row_break_becomes_a_space(self) -> None:
        doc = r"""
        \begin{tabular}{lc}
        Model & \makecell{Total train \\ (flops)} \\
        \midrule
        GPT-3 & 3.14 \\
        \end{tabular}
        """
        cell = extract_tables(doc).tables[0].cells[0]
        assert "\\" not in cell.address
        # A loose tabular is addressed as inline, not as the paper's Table 1.
        assert cell.address == "Inline table 1 › GPT-3 › Total train (flops)"


class TestFloatNumberingIsThePapersNumbering:
    DOC = r"""
    \begin{tabular}{lc}
    Note & 1.0 \\
    \end{tabular}

    \begin{table}
    \caption{First real table}
    \begin{tabular}{lc}
    Model & Score \\
    \midrule
    A & 2.0 \\
    \end{tabular}
    \end{table}
    """

    def test_a_loose_tabular_does_not_consume_table_one(self) -> None:
        # The float is the paper's Table 1 even though a tabular precedes it.
        paper = extract_tables(self.DOC)
        floats = [t for t in paper.tables if t.caption]
        assert any(c.address.startswith("Table 1 ") for c in floats[0].cells)

    def test_loose_tabulars_are_addressed_separately(self) -> None:
        addresses = [c.address for t in extract_tables(self.DOC).tables for c in t.cells]
        assert any(a.startswith("Inline table 1 ") for a in addresses)
        assert sum(a.startswith("Table 1 ") for a in addresses) == 1
