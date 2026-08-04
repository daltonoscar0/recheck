from __future__ import annotations

import pytest

from recheck.paper.latex import (
    collect_emphasis,
    expand_macros,
    find_command,
    find_matching_brace,
    flatten,
    read_args,
    split_rows,
    split_top_level,
    strip_comments,
)


class TestBraceMatching:
    def test_matches_simple_pair(self) -> None:
        assert find_matching_brace("{abc}", 0) == 4

    def test_matches_through_nesting(self) -> None:
        text = "{a{b}c}"
        assert find_matching_brace(text, 0) == 6

    def test_ignores_escaped_braces(self) -> None:
        text = r"{a\{b}"
        assert find_matching_brace(text, 0) == 5

    def test_raises_on_unbalanced(self) -> None:
        with pytest.raises(ValueError, match="unbalanced"):
            find_matching_brace("{abc", 0)

    def test_raises_when_not_a_brace(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            find_matching_brace("abc", 0)


class TestReadArgs:
    def test_reads_consecutive_arguments(self) -> None:
        args, end = read_args("{2}{c}{body}rest", 0, 3)
        assert args == ["2", "c", "body"]
        assert "{2}{c}{body}rest"[end:] == "rest"

    def test_skips_optional_arguments(self) -> None:
        args, _ = read_args("[t]{2}{*}{Model}", 0, 3)
        assert args == ["2", "*", "Model"]

    def test_missing_arguments_come_back_empty(self) -> None:
        args, _ = read_args("{2}", 0, 3)
        assert args == ["2", "", ""]


class TestSplitTopLevel:
    def test_splits_on_ampersand(self) -> None:
        assert split_top_level("a & b & c", "&") == ["a ", " b ", " c"]

    def test_ignores_ampersand_inside_braces(self) -> None:
        parts = split_top_level(r"\multicolumn{2}{c}{a & b} & c", "&")
        assert len(parts) == 2

    def test_ignores_ampersand_inside_math(self) -> None:
        assert len(split_top_level(r"$a & b$ & c", "&")) == 2

    def test_ignores_escaped_ampersand(self) -> None:
        assert split_top_level(r"a \& b & c", "&") == [r"a \& b ", " c"]


class TestSplitRows:
    def test_splits_on_row_break(self) -> None:
        assert split_rows(r"a & b \\ c & d") == ["a & b ", " c & d"]

    def test_does_not_split_on_other_commands(self) -> None:
        rows = split_rows(r"\textbf{a} & b \\ c")
        assert len(rows) == 2
        assert r"\textbf{a}" in rows[0]

    def test_consumes_row_spacing_argument(self) -> None:
        rows = split_rows(r"a \\[2pt] b")
        assert rows == ["a ", " b"]


class TestStripComments:
    def test_removes_comment_tail(self) -> None:
        assert strip_comments("value % a note") == "value "

    def test_keeps_escaped_percent(self) -> None:
        assert strip_comments(r"98.5\% accuracy") == r"98.5\% accuracy"


class TestFlatten:
    def test_unwraps_formatting(self) -> None:
        assert flatten(r"\textbf{1.42}") == "1.42"

    def test_unwraps_nested_formatting(self) -> None:
        assert flatten(r"\textbf{\texttt{ok}}") == "ok"

    def test_renders_symbols(self) -> None:
        assert flatten(r"$1.42 \pm 0.21$") == "1.42 ± 0.21"
        assert flatten(r"$\Delta$") == "Δ"
        assert flatten(r"98\%") == "98%"

    def test_drops_label_and_its_argument(self) -> None:
        assert "tab:x" not in flatten(r"A caption.\label{tab:x}")

    def test_drops_partial_rule_with_paren_argument(self) -> None:
        assert flatten(r"\cmidrule(lr){3-4}") == ""

    def test_drops_unknown_commands(self) -> None:
        assert flatten(r"\resizebox{\textwidth}{!}{x}") == "x"


class TestEmphasis:
    def test_detects_bold(self) -> None:
        assert collect_emphasis(r"\textbf{1.42}") == ["bold"]

    def test_detects_italic(self) -> None:
        assert collect_emphasis(r"\emph{x}") == ["italic"]

    def test_reports_nothing_for_plain_text(self) -> None:
        assert collect_emphasis("1.42") == []


class TestFindCommand:
    def test_finds_multicolumn_arguments(self) -> None:
        cmd = find_command(r"\multicolumn{2}{c}{Surprisal}", "multicolumn", 3)
        assert cmd is not None
        assert cmd.args == ["2", "c", "Surprisal"]

    def test_returns_none_when_absent(self) -> None:
        assert find_command("plain", "multicolumn", 3) is None


class TestMachineryStripping:
    def test_drops_citation_keys(self) -> None:
        assert flatten(r"ByteNet \citep{NalBytenet2017}") == "ByteNet"

    def test_drops_citet_and_cite(self) -> None:
        assert flatten(r"\citet{a} and \cite{b} agree") == "and agree"

    def test_drops_rule_spacing(self) -> None:
        assert flatten(r"\rule{0pt}{2.0ex}base") == "base"

    def test_drops_footnotes(self) -> None:
        assert flatten(r"value\footnote{a caveat}") == "value"


class TestMacroExpansion:
    def test_expands_zero_argument_macro(self) -> None:
        doc = r"\newcommand{\dmodel}{d_{\text{model}}} $\dmodel$"
        assert "model" in flatten(expand_macros(doc))

    def test_expands_macro_with_arguments(self) -> None:
        doc = r"\newcommand{\gain}[2]{#1 over #2} \gain{5}{baseline}"
        assert "5 over baseline" in expand_macros(doc)

    def test_expands_def_form(self) -> None:
        assert "0.5" in expand_macros(r"\def\thr{0.5} value \thr")

    def test_ignores_prefix_collisions(self) -> None:
        # \dmodels must not be rewritten by the \dmodel rule.
        out = expand_macros(r"\newcommand{\d}{X} \dmodel")
        assert r"\dmodel" in out

    def test_refuses_to_shadow_structural_commands(self) -> None:
        # The definition text stays put; what matters is that the *use site*
        # survives intact so the grid builder still sees a real \multicolumn.
        doc = r"\newcommand{\multicolumn}{BROKEN} \multicolumn{2}{c}{x}"
        assert r"\multicolumn{2}{c}{x}" in expand_macros(doc)

    def test_self_referential_macro_terminates(self) -> None:
        assert expand_macros(r"\newcommand{\loop}{\loop} \loop") is not None

    def test_collapses_subscript_groups(self) -> None:
        assert flatten(r"$d_{\text{model}}$") == "d_model"
        assert flatten(r"$P_{drop}$") == "P_drop"

    def test_collapses_superscript_groups(self) -> None:
        assert flatten(r"$x^{2}$") == "x^2"

    def test_symbol_survives_a_following_subscript(self) -> None:
        # Regression: `\b` does not match before `_`, so the symbol was dropped
        # as an unknown command and the cell read "_ls".
        assert flatten(r"$\epsilon_{ls}$") == "ε_ls"
        assert flatten(r"$\Delta_{x}$") == "Δ_x"
