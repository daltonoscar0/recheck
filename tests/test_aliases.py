from __future__ import annotations

from recheck.map.aliases import AliasTable, harvest
from recheck.map.inventory import ScriptFile

RENDER = ScriptFile(
    path="notebooks/main.py",
    text=(
        'RENDER = {\n'
        '    "vanilla": "LSTM",\n'
        '    "ordered-neurons": "ON-LSTM",\n'
        '}\n'
        'MARKERS = {"RNNG": "X", "LSTM": "*"}\n'
        'PATHS = {"out": "results/main.csv"}\n'
    ),

)

KNOWN = {"vanilla", "ordered-neurons", "rnng"}


class TestHarvest:
    def test_finds_a_declared_display_name(self) -> None:
        found = harvest([RENDER], KNOWN)
        pairs = {(a.left, a.right) for a in found}
        assert ("vanilla", "LSTM") in pairs
        assert ("ordered-neurons", "ON-LSTM") in pairs

    def test_ignores_single_character_glyphs(self) -> None:
        # Plotting code is full of {"RNNG": "X"}; a marker is not a model name.
        found = harvest([RENDER], KNOWN)
        assert all(len(a.left) >= 2 and len(a.right) >= 2 for a in found)

    def test_ignores_pairs_unrelated_to_the_data(self) -> None:
        # Neither side of {"out": "results/main.csv"} is a value the data holds.
        found = harvest([RENDER], KNOWN)
        assert all("csv" not in a.right for a in found)

    def test_requires_exactly_one_side_to_be_known(self) -> None:
        # Both sides known means the data already speaks both names.
        assert harvest([RENDER], {"vanilla", "LSTM"}) == []

    def test_evidence_cites_file_and_line(self) -> None:
        alias = next(a for a in harvest([RENDER], KNOWN) if a.right == "LSTM")
        assert "notebooks/main.py:2" in alias.evidence


class TestAliasTable:
    def test_lookup_is_bidirectional(self) -> None:
        table = AliasTable(harvest([RENDER], KNOWN))
        assert "vanilla" in [name for name, _ in table.for_name("LSTM")]
        assert "LSTM" in [name for name, _ in table.for_name("vanilla")]

    def test_unknown_name_yields_nothing(self) -> None:
        assert AliasTable(harvest([RENDER], KNOWN)).for_name("Mamba") == []

    def test_empty_table_is_falsy(self) -> None:
        assert not AliasTable([])
