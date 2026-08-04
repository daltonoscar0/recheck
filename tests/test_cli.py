from __future__ import annotations

import json

import typer.main
from typer.testing import CliRunner

from conftest import SCORE_SCRIPT, SCORES_CSV, SCORES_PAPER_TEX, make_git_repo, shared_git_repo
from recheck.cli import app

runner = CliRunner()


def declared_options(command: str | None = None) -> set[str]:
    """Every option the CLI declares, read from the command tree.

    Asserting against `--help` output tests how rich chose to lay out a panel at
    the current terminal width — it truncates long option names and wraps
    differently in CI than it does locally, which is what made these tests fail
    on the first push. The argument surface is the contract; its rendering is not.
    """
    root = typer.main.get_command(app)
    target = root.commands[command] if command else root
    return {
        option
        for parameter in target.params
        for option in (*parameter.opts, *parameter.secondary_opts)
    }


class TestHelp:
    def test_top_level_help_lists_subcommands(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for command in ("extract", "diff", "run"):
            assert command in result.stdout

    def test_run_exposes_the_full_argument_surface(self) -> None:
        assert runner.invoke(app, ["run", "--help"]).exit_code == 0
        assert {"--repo", "--max-hours", "--max-gpu", "--out", "--tolerance"} <= declared_options(
            "run"
        )

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
    def _repo(self):
        return shared_git_repo(
            {"run_eval.py": SCORE_SCRIPT, "results/scores_tiny.csv": SCORES_CSV}
        )

    def _paper(self, tmp_path):
        path = tmp_path / "paper.tex"
        path.write_text(SCORES_PAPER_TEX)
        return path

    def test_a_repo_is_required(self, fixtures_dir) -> None:
        result = runner.invoke(app, ["run", str(fixtures_dir / "clean_booktabs.tex")])
        assert result.exit_code == 2
        assert "--repo is required" in result.stderr

    def test_reproduces_end_to_end(self, tmp_path) -> None:
        result = runner.invoke(
            app,
            [
                "run", str(self._paper(tmp_path)),
                "--repo", str(self._repo()),
                "--max-gpu", "0",
                "--results-out", str(tmp_path / "results.json"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Acquired" in result.stdout

        payload = json.loads((tmp_path / "results.json").read_text())
        assert payload["schema_version"] == "1.0"
        assert payload["run"]["commit"]
        by_address = {c["address"]: c for c in payload["cells"]}
        assert by_address["Table 1 › tiny › dev"]["value"] == 0.85
        assert by_address["Table 1 › huge › dev"]["failure"]["code"] == "SCRIPT_NOT_FOUND"

    def test_writes_a_markdown_report_with_provenance(self, tmp_path) -> None:
        result = runner.invoke(
            app,
            [
                "run", str(self._paper(tmp_path)),
                "--repo", str(self._repo()),
                "--max-gpu", "0",
                "--out", str(tmp_path / "report.md"),
            ],
        )
        assert result.exit_code == 0, result.output
        report = (tmp_path / "report.md").read_text()
        assert "**Provenance:**" in report
        assert "run_eval.py" in report

    def test_a_dirty_repo_is_refused(self, tmp_path) -> None:
        source = make_git_repo(tmp_path / "dirty", {"a.py": "print(1)\n"})
        (source / "a.py").write_text("print(2)\n")
        result = runner.invoke(
            app, ["run", str(self._paper(tmp_path)), "--repo", str(source)]
        )
        assert result.exit_code == 2
        assert "uncommitted" in result.stderr

    def test_the_workdir_can_be_kept(self, tmp_path) -> None:
        workdir = tmp_path / "keep"
        runner.invoke(
            app,
            [
                "run", str(self._paper(tmp_path)),
                "--repo", str(self._repo()),
                "--max-gpu", "0",
                "--workdir", str(workdir),
            ],
        )
        assert (workdir / "repo" / "run_eval.py").exists()

    def test_help_lists_the_execution_flags(self) -> None:
        # Wide, so the option column is not wrapped mid-flag.
        assert runner.invoke(app, ["run", "--help"]).exit_code == 0
        assert {
            "--max-download-mb", "--results-out", "--workdir", "--allow-dirty",
            "--committed-artifacts", "--no-committed-artifacts", "--plan-cache",
            "--refresh-plan",
        } <= declared_options("run")


class TestPerTableTolerance:
    def _paper(self, tmp_path, fixtures_dir):
        out = tmp_path / "paper.json"
        runner.invoke(
            app, ["extract", str(fixtures_dir / "demo_report.tex"), "--out", str(out)]
        )
        return out

    def test_config_flag_is_applied(self, tmp_path, fixtures_dir, results_dir) -> None:
        config = tmp_path / "strict.toml"
        config.write_text(
            '[tolerance]\nrelative = 0.05\n\n[tolerance.tables."tab:scales"]\n'
            "relative = 0.0001\nabsolute = 0.0001\n"
        )
        result = runner.invoke(
            app,
            [
                "diff",
                str(self._paper(tmp_path, fixtures_dir)),
                str(results_dir / "demo_results.json"),
                "--tolerance-config",
                str(config),
            ],
        )
        assert "per-table tolerances" in result.stdout
        assert result.exit_code == 1

    def test_nearby_config_is_discovered(self, tmp_path, fixtures_dir, results_dir) -> None:
        (tmp_path / "recheck.toml").write_text(
            '[tolerance]\nrelative = 0.05\n\n[tolerance.tables."tab:scales"]\nabsolute = 5.0\n'
        )
        result = runner.invoke(
            app,
            [
                "diff",
                str(self._paper(tmp_path, fixtures_dir)),
                str(results_dir / "demo_results.json"),
            ],
        )
        assert "recheck.toml" in result.stdout

    def test_bad_config_exits_two(self, tmp_path, fixtures_dir, results_dir) -> None:
        config = tmp_path / "bad.toml"
        config.write_text("[tolerance]\nwobble = 3\n")
        result = runner.invoke(
            app,
            [
                "diff",
                str(self._paper(tmp_path, fixtures_dir)),
                str(results_dir / "demo_results.json"),
                "--tolerance-config",
                str(config),
            ],
        )
        assert result.exit_code == 2

    def test_run_accepts_the_option(self) -> None:
        assert "--tolerance-config" in declared_options("run")
        assert "--tolerance-config" in declared_options("diff")
