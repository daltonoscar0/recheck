from __future__ import annotations

import json

from typer.testing import CliRunner

from recheck.cli import app

runner = CliRunner()


class TestHelp:
    def test_top_level_help_lists_subcommands(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for command in ("extract", "diff", "run"):
            assert command in result.stdout

    def test_run_exposes_the_full_argument_surface(self) -> None:
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        for option in ("--repo", "--max-hours", "--max-gpu", "--out", "--tolerance"):
            assert option in result.stdout

    def test_version(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "recheck" in result.stdout


class TestExtract:
    def test_writes_paper_json(self, tmp_path, fixtures_dir) -> None:
        out = tmp_path / "paper.json"
        result = runner.invoke(
            app, ["extract", str(fixtures_dir / "clean_booktabs.tex"), "--out", str(out)]
        )
        assert result.exit_code == 0
        payload = json.loads(out.read_text())
        assert payload["schema_version"] == "1.0"
        assert len(payload["tables"]) == 1

    def test_prints_json_without_out(self, fixtures_dir) -> None:
        result = runner.invoke(app, ["extract", str(fixtures_dir / "clean_booktabs.tex")])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["tables"]

    def test_unparseable_source_exits_two(self, tmp_path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        result = runner.invoke(app, ["extract", str(empty)])
        assert result.exit_code == 2


class TestDiff:
    def _paper(self, tmp_path, fixtures_dir, name: str):
        out = tmp_path / f"{name}.json"
        runner.invoke(app, ["extract", str(fixtures_dir / f"{name}.tex"), "--out", str(out)])
        return out

    def test_calibration_reproduces_cleanly(self, tmp_path, fixtures_dir, results_dir) -> None:
        paper = self._paper(tmp_path, fixtures_dir, "garden_path_calibration")
        result = runner.invoke(
            app, ["diff", str(paper), str(results_dir / "garden_path_results.json")]
        )
        assert result.exit_code == 0, result.stdout
        assert "6/6" in result.stdout

    def test_red_cells_exit_one(self, tmp_path, fixtures_dir, results_dir) -> None:
        paper = self._paper(tmp_path, fixtures_dir, "demo_report")
        result = runner.invoke(app, ["diff", str(paper), str(results_dir / "demo_results.json")])
        assert result.exit_code == 1

    def test_markdown_report_is_written(self, tmp_path, fixtures_dir, results_dir) -> None:
        paper = self._paper(tmp_path, fixtures_dir, "demo_report")
        report = tmp_path / "report.md"
        runner.invoke(
            app,
            ["diff", str(paper), str(results_dir / "demo_results.json"), "--out", str(report)],
        )
        text = report.read_text()
        assert text.startswith("# Reproduction report")
        assert "Why cells could not be run" in text

    def test_tolerance_option_changes_verdicts(self, tmp_path, fixtures_dir, results_dir) -> None:
        paper = self._paper(tmp_path, fixtures_dir, "demo_report")
        loose = runner.invoke(
            app,
            ["diff", str(paper), str(results_dir / "demo_results.json"), "--tolerance", "1.0"],
        )
        # A 100% band still cannot rescue the sign-flipped cell.
        assert loose.exit_code == 1

    def test_malformed_tolerance_exits_two(self, tmp_path, fixtures_dir, results_dir) -> None:
        paper = self._paper(tmp_path, fixtures_dir, "demo_report")
        result = runner.invoke(
            app,
            ["diff", str(paper), str(results_dir / "demo_results.json"), "--tolerance", "nope=1"],
        )
        assert result.exit_code == 2


class TestRun:
    def test_reports_that_execution_is_pending(self, fixtures_dir) -> None:
        result = runner.invoke(app, ["run", str(fixtures_dir / "clean_booktabs.tex")])
        assert result.exit_code == 3
        assert "milestone 2" in result.stdout
