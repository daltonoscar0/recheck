from __future__ import annotations

import pytest

from recheck.diff import Tolerance, TolerancePolicy
from recheck.diff.config import (
    ConfigError,
    discover_config,
    load_tolerance_config,
    parse_tolerance_config,
    resolve_policy,
)

CONFIG = """
[tolerance]
relative = 0.02
absolute = 0.1

[tolerance.tables."tab:npz"]
relative = 0.005

[tolerance.tables."table-2"]
absolute = 2.0
yellow_multiplier = 2.0
"""


def write_config(tmp_path, text: str = CONFIG):
    path = tmp_path / "recheck.toml"
    path.write_text(text)
    return path


class TestParsing:
    def test_reads_the_default_band(self, tmp_path) -> None:
        policy = load_tolerance_config(write_config(tmp_path))
        assert policy.default.relative == pytest.approx(0.02)
        assert policy.default.absolute == pytest.approx(0.1)

    def test_reads_per_table_overrides(self, tmp_path) -> None:
        policy = load_tolerance_config(write_config(tmp_path))
        assert set(policy.per_table) == {"tab:npz", "table-2"}
        assert policy.for_table("tab:npz").relative == pytest.approx(0.005)

    def test_per_table_inherits_unset_fields_from_the_default(self, tmp_path) -> None:
        # tab:npz sets only `relative`, so it keeps the configured absolute floor.
        policy = load_tolerance_config(write_config(tmp_path))
        assert policy.for_table("tab:npz").absolute == pytest.approx(0.1)
        assert policy.for_table("table-2").relative == pytest.approx(0.02)

    def test_unknown_table_falls_back_to_the_default(self, tmp_path) -> None:
        policy = load_tolerance_config(write_config(tmp_path))
        assert policy.for_table("tab:unheard-of") == policy.default

    def test_short_key_aliases(self) -> None:
        policy = parse_tolerance_config({"tolerance": {"rel": 0.01, "abs": 0.2, "yellow": 4}})
        assert policy.default.relative == pytest.approx(0.01)
        assert policy.default.absolute == pytest.approx(0.2)
        assert policy.default.yellow_multiplier == pytest.approx(4.0)

    def test_bare_document_without_tolerance_section(self) -> None:
        assert parse_tolerance_config({"relative": 0.3}).default.relative == pytest.approx(0.3)


class TestErrors:
    def test_unknown_key_is_rejected(self) -> None:
        with pytest.raises(ConfigError, match="unknown tolerance key"):
            parse_tolerance_config({"tolerance": {"wobble": 1}})

    def test_non_numeric_value_is_rejected(self) -> None:
        with pytest.raises(ConfigError, match="must be a number"):
            parse_tolerance_config({"tolerance": {"relative": "loose"}})

    def test_boolean_is_not_a_number(self) -> None:
        with pytest.raises(ConfigError, match="must be a number"):
            parse_tolerance_config({"tolerance": {"relative": True}})

    def test_table_entry_must_be_a_table(self) -> None:
        with pytest.raises(ConfigError, match="must be a table of settings"):
            parse_tolerance_config({"tolerance": {"tables": {"tab:x": 0.5}}})

    def test_missing_file(self, tmp_path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_tolerance_config(tmp_path / "absent.toml")

    def test_invalid_toml(self, tmp_path) -> None:
        path = tmp_path / "recheck.toml"
        path.write_text("[tolerance\nrelative = ")
        with pytest.raises(ConfigError, match="invalid TOML"):
            load_tolerance_config(path)


class TestDiscovery:
    def test_finds_config_in_the_same_directory(self, tmp_path) -> None:
        write_config(tmp_path)
        assert discover_config(tmp_path) == tmp_path / "recheck.toml"

    def test_walks_up_from_a_file(self, tmp_path) -> None:
        write_config(tmp_path)
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        paper = nested / "paper.json"
        paper.write_text("{}")
        assert discover_config(paper) == tmp_path / "recheck.toml"

    def test_returns_none_when_absent(self, tmp_path) -> None:
        assert discover_config(tmp_path) is None


class TestPrecedence:
    def test_cli_tolerance_overrides_the_config_default(self, tmp_path) -> None:
        policy, _ = resolve_policy(write_config(tmp_path), Tolerance(relative=0.9))
        assert policy.default.relative == pytest.approx(0.9)

    def test_cli_tolerance_does_not_erase_per_table_entries(self, tmp_path) -> None:
        # A per-table band is a more specific statement than a global flag.
        policy, _ = resolve_policy(write_config(tmp_path), Tolerance(relative=0.9))
        assert policy.for_table("tab:npz").relative == pytest.approx(0.005)

    def test_no_config_and_no_flag_is_the_built_in_default(self) -> None:
        policy, used = resolve_policy(None, None)
        assert policy == TolerancePolicy()
        assert used is None

    def test_reports_which_config_was_used(self, tmp_path) -> None:
        path = write_config(tmp_path)
        _, used = resolve_policy(None, None, search_from=tmp_path)
        assert used == path


class TestEndToEnd:
    def test_per_table_band_changes_a_verdict(self, tmp_path, fixtures_dir, results_dir) -> None:
        from recheck.diff import Status, compare
        from recheck.paper import extract_tables
        from recheck.schema import Results

        paper = extract_tables((fixtures_dir / "demo_report.tex").read_text())
        results = Results.read(results_dir / "demo_results.json")

        loose = compare(paper, results, TolerancePolicy(default=Tolerance(relative=0.2)))
        strict_config = tmp_path / "recheck.toml"
        strict_config.write_text(
            '[tolerance]\nrelative = 0.2\n\n[tolerance.tables."tab:scales"]\n'
            "relative = 0.0001\nabsolute = 0.0001\n"
        )
        policy, _ = resolve_policy(strict_config, None)
        strict = compare(paper, results, policy)

        def reds(report):
            return sum(1 for c in report.comparisons if c.status is Status.RED)

        assert reds(strict) > reds(loose)
