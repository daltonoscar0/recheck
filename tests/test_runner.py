"""End to end: a paper and a repo in, a value or an evidenced reason out.

Every failure path in the taxonomy that the executor can reach is provoked
deliberately here, and each test asserts the code *and* that the evidence names
something concrete — a file, a package, a model, a measured cost. A generic
reason is the failure mode this whole tool exists to prevent, so "there is a
reason" is not enough for a test to pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import SCORE_SCRIPT, SCORES_CSV, SCORES_PAPER_TEX, make_git_repo, shared_git_repo
from recheck.diff import Status, compare
from recheck.diff.taxonomy import FailureCode
from recheck.exec import Budget, EntryStatus, LocalSandbox, RunOptions, execute
from recheck.exec.sandbox import CommandResult
from recheck.paper import extract_tables
from recheck.repo import acquire

BIG_MODEL_SCRIPT = '''\
import csv

models = ["gpt2-large"]
for name in models:
    print("scoring model split score with", name)

with open("results/scores_tiny.csv", "w", newline="") as handle:
    csv.writer(handle).writerow(["model", "split", "score"])
'''

SLOW_SCRIPT = '''\
import csv
import time

print("model split score dev test")
time.sleep(30)
with open("results/scores_tiny.csv", "w", newline="") as handle:
    csv.writer(handle).writerow(["model", "split", "score"])
'''

UNSEEDED_SCRIPT = '''\
import csv
import random

ROWS = [("tiny", "dev", 0.80), ("tiny", "dev", 0.90),
        ("tiny", "test", 0.60), ("tiny", "test", 0.70)]
random.shuffle(ROWS)
with open("results/scores_tiny.csv", "w", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["model", "split", "score"])
    for row in ROWS:
        writer.writerow(row)
'''

LATE_IMPORT_SCRIPT = '''\
import csv
import importlib

print("model split score dev test")
importlib.import_module("nonexistent_module_xyz")
with open("results/scores_tiny.csv", "w", newline="") as handle:
    csv.writer(handle).writerow(["model", "split", "score"])
'''


def run(tmp_path: Path, files: dict[str, str], *, tex: str = SCORES_PAPER_TEX, **options):
    """Acquire a synthetic repo and reproduce a paper against it."""
    return _run(shared_git_repo(files), tmp_path, tex, options)


def _run(source: Path, tmp_path: Path, tex: str, options: dict):
    root = tmp_path / "sandbox"
    sandbox = LocalSandbox(root)
    repo = acquire(str(source), sandbox.workdir)
    settings = RunOptions(
        budget=options.pop("budget", Budget(max_hours=2.0, max_gpu_hours=0.0,
                                            max_download_mb=512.0)),
        **options,
    )
    return execute(extract_tables(tex), repo, sandbox, settings)


def cell(report, address_suffix: str):
    for result in report.results.cells:
        if result.address.endswith(address_suffix):
            return result
    raise AssertionError(f"no result cell ending in {address_suffix!r}")


class TestHappyPath:
    def test_a_run_produces_values_and_says_it_ran(self, tmp_path) -> None:
        report = run(
            tmp_path,
            {"run_eval.py": SCORE_SCRIPT, "results/scores_tiny.csv": SCORES_CSV},
        )
        assert report.outcomes[0].status == EntryStatus.EXECUTED
        assert report.outcomes[0].script == "run_eval.py"
        assert cell(report, "tiny › dev").value == 0.85
        assert cell(report, "tiny › test").value == 0.65
        assert "re-running run_eval.py" in report.results.run["note"]

    def test_the_report_grades_the_run(self, tmp_path) -> None:
        report = run(
            tmp_path,
            {"run_eval.py": SCORE_SCRIPT, "results/scores_tiny.csv": SCORES_CSV},
        )
        diff = compare(extract_tables(SCORES_PAPER_TEX), report.results)
        statuses = {c.address: c.status for c in diff.comparisons}
        assert statuses["Table 1 › tiny › dev"] is Status.GREEN
        assert statuses["Table 1 › huge › dev"] is Status.UNRUNNABLE

    def test_every_numeric_cell_is_accounted_for(self, tmp_path) -> None:
        report = run(
            tmp_path,
            {"run_eval.py": SCORE_SCRIPT, "results/scores_tiny.csv": SCORES_CSV},
        )
        paper = extract_tables(SCORES_PAPER_TEX)
        numeric = {c.address for t in paper.tables for c in t.cells if c.is_numeric}
        assert {c.address for c in report.results.cells} == numeric
        diff = compare(paper, report.results)
        assert diff.counts()[Status.NOT_ATTEMPTED] == 0

    def test_the_repo_is_pinned_in_the_run_record(self, tmp_path) -> None:
        report = run(tmp_path, {"run_eval.py": SCORE_SCRIPT,
                                "results/scores_tiny.csv": SCORES_CSV})
        assert len(report.results.run["commit_full"]) == 40
        assert report.results.run["mapper"] == "deterministic"


class TestBudget:
    def test_the_estimate_alone_refuses_an_oversized_download(self, tmp_path) -> None:
        report = run(
            tmp_path,
            {"score_models.py": BIG_MODEL_SCRIPT, "results/scores_tiny.csv": SCORES_CSV},
            allow_committed_artifacts=False,
        )
        outcome = report.outcomes[0]
        assert outcome.status == EntryStatus.REFUSED
        assert outcome.seconds == 0, "a refused run must not spend wall clock"

        failure = cell(report, "tiny › dev")
        assert failure.failure_code == FailureCode.BUDGET_EXCEEDED.value
        assert "gpt2-large" in failure.evidence
        assert "--max-download-mb" in failure.evidence
        assert "512" in failure.evidence

    def test_a_run_past_the_wall_clock_ceiling_is_killed(self, tmp_path) -> None:
        report = run(
            tmp_path,
            {"score_slow.py": SLOW_SCRIPT, "results/scores_tiny.csv": SCORES_CSV},
            budget=Budget(max_hours=1.5 / 3600, max_gpu_hours=0.0, max_download_mb=512.0),
            allow_committed_artifacts=False,
        )
        outcome = report.outcomes[0]
        assert outcome.status == EntryStatus.REFUSED
        assert outcome.blockers[0].code is FailureCode.BUDGET_EXCEEDED
        assert "killed after" in outcome.blockers[0].evidence
        assert "--max-hours" in outcome.blockers[0].evidence

        failure = cell(report, "tiny › dev")
        assert failure.failure_code == FailureCode.BUDGET_EXCEEDED.value
        assert "score_slow.py" in failure.evidence

    def test_a_zero_gpu_ceiling_refuses_gpu_work(self, tmp_path) -> None:
        cuda_script = BIG_MODEL_SCRIPT.replace(
            'print("scoring', 'model = load().to("cuda")\n    print("scoring'
        )
        report = run(
            tmp_path,
            {"score_models.py": cuda_script, "results/scores_tiny.csv": SCORES_CSV},
            budget=Budget(max_hours=2.0, max_gpu_hours=0.0, max_download_mb=100_000.0),
            allow_committed_artifacts=False,
        )
        failure = cell(report, "tiny › dev")
        assert failure.failure_code == FailureCode.BUDGET_EXCEEDED.value
        assert "--max-gpu" in failure.evidence
        assert "score_models.py" in failure.evidence

    def test_the_budget_is_recorded_in_the_run(self, tmp_path) -> None:
        report = run(tmp_path, {"run_eval.py": SCORE_SCRIPT,
                                "results/scores_tiny.csv": SCORES_CSV})
        assert report.results.run["budget"]["max_gpu_hours"] == 0.0
        assert report.results.run["budget"]["spent_hours"] >= 0.0


class TestDependencies:
    def test_an_unpinned_import_is_caught_before_anything_runs(self, tmp_path) -> None:
        report = run(
            tmp_path,
            {
                "score_eval.py": "import minicons\n" + SCORE_SCRIPT,
                "results/scores_tiny.csv": SCORES_CSV,
                "requirements.txt": "numpy==2.0.0\n",
            },
            allow_committed_artifacts=False,
        )
        outcome = report.outcomes[0]
        assert outcome.status == EntryStatus.REFUSED
        assert outcome.seconds == 0

        failure = cell(report, "tiny › dev")
        assert failure.failure_code == FailureCode.MISSING_DEPENDENCY.value
        assert "minicons" in failure.evidence
        assert "score_eval.py:1" in failure.evidence
        assert "requirements.txt" in failure.evidence

    def test_an_import_that_only_fails_at_runtime_is_still_named(self, tmp_path) -> None:
        report = run(
            tmp_path,
            {"score_eval.py": LATE_IMPORT_SCRIPT, "results/scores_tiny.csv": SCORES_CSV},
            allow_committed_artifacts=False,
        )
        assert report.outcomes[0].status == EntryStatus.FAILED
        failure = cell(report, "tiny › dev")
        assert failure.failure_code == FailureCode.MISSING_DEPENDENCY.value
        assert "nonexistent_module_xyz" in failure.evidence

    def test_a_resolver_failure_is_env_unresolvable(self, tmp_path, monkeypatch) -> None:
        from recheck.exec import env as env_module

        class ResolverFails:
            """A sandbox whose installs always fail, so no network is needed."""

            def __init__(self, root: Path) -> None:
                self.inner = LocalSandbox(root)
                self.root = self.inner.root

            @property
            def workdir(self) -> Path:
                return self.inner.workdir

            def run(self, argv, *, timeout, cwd=None, env=None):
                if argv[:2] == ["uv", "pip"]:
                    return CommandResult(
                        argv=tuple(argv),
                        returncode=1,
                        stdout="",
                        stderr="error: no solution found: numpy==2.0.0 conflicts with "
                        "torch==1.0.0 which requires numpy<2",
                        seconds=0.2,
                    )
                if argv[:2] == ["uv", "venv"]:
                    return CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="",
                                         seconds=0.1)
                return self.inner.run(argv, timeout=timeout, cwd=cwd, env=env)

        monkeypatch.setattr(env_module, "have", lambda tool: True)

        source = make_git_repo(
            tmp_path / "source",
            {
                "run_eval.py": SCORE_SCRIPT,
                "results/scores_tiny.csv": SCORES_CSV,
                "requirements.txt": "numpy==2.0.0\ntorch==1.0.0\n",
            },
        )
        sandbox = ResolverFails(tmp_path / "sandbox")
        repo = acquire(str(source), sandbox.workdir)
        report = execute(
            extract_tables(SCORES_PAPER_TEX),
            repo,
            sandbox,
            RunOptions(budget=Budget(2.0, 0.0, 512.0), allow_committed_artifacts=False),
        )

        failure = cell(report, "tiny › dev")
        assert failure.failure_code == FailureCode.ENV_UNRESOLVABLE.value
        assert "no solution found" in failure.evidence
        assert "requirements.txt" in failure.evidence


class TestUnroutableCells:
    def test_a_repo_with_no_matching_artifact_names_what_it_looked_at(self, tmp_path) -> None:
        report = run(tmp_path, {"notes.md": "nothing to see", "results/scores_tiny.csv":
                                SCORES_CSV})
        failure = cell(report, "huge › dev")
        assert failure.failure_code == FailureCode.SCRIPT_NOT_FOUND.value
        assert "results/scores_tiny.csv" in failure.evidence
        assert "huge" in failure.evidence

    def test_a_repo_with_no_scripts_at_all(self, tmp_path) -> None:
        report = run(tmp_path, {"notes.md": "nothing to see"})
        assert report.outcomes[0].blockers[0].code is FailureCode.SCRIPT_NOT_FOUND
        assert "no script" in report.outcomes[0].blockers[0].evidence
        assert all(c.is_failure for c in report.results.cells)
        assert "nothing was reproducible" in report.results.run["note"]


class TestCommittedArtifacts:
    def test_a_blocked_run_falls_back_and_says_so(self, tmp_path) -> None:
        report = run(
            tmp_path,
            {"score_models.py": BIG_MODEL_SCRIPT, "results/scores_tiny.csv": SCORES_CSV},
        )
        assert cell(report, "tiny › dev").value == 0.85
        note = report.results.run["note"]
        assert "not re-scored" in note
        assert "BUDGET_EXCEEDED" in note
        assert "score_models.py" in note

    def test_opting_out_reports_the_blocker_instead(self, tmp_path) -> None:
        report = run(
            tmp_path,
            {"score_models.py": BIG_MODEL_SCRIPT, "results/scores_tiny.csv": SCORES_CSV},
            allow_committed_artifacts=False,
        )
        assert cell(report, "tiny › dev").value is None
        assert cell(report, "tiny › dev").failure_code == FailureCode.BUDGET_EXCEEDED.value


class TestSeeds:
    def test_an_unseeded_script_reports_its_value_with_a_caveat(self, tmp_path) -> None:
        report = run(
            tmp_path,
            {"score_eval.py": UNSEEDED_SCRIPT, "results/scores_tiny.csv": SCORES_CSV},
        )
        produced = cell(report, "tiny › dev")
        assert produced.value == 0.85, "the value is still reported"
        assert any(
            FailureCode.NONDETERMINISTIC_NO_SEED.value in caveat for caveat in produced.caveats
        )
        assert any("score_eval.py" in caveat for caveat in produced.caveats)

    def test_the_caveat_reaches_the_report(self, tmp_path) -> None:
        report = run(
            tmp_path,
            {"score_eval.py": UNSEEDED_SCRIPT, "results/scores_tiny.csv": SCORES_CSV},
        )
        diff = compare(extract_tables(SCORES_PAPER_TEX), report.results)
        notes = [n for c in diff.comparisons for n in c.notes]
        assert any(FailureCode.NONDETERMINISTIC_NO_SEED.value in note for note in notes)


class TestDiscovery:
    def test_a_repo_that_commits_no_outputs_is_mapped_after_running(self, tmp_path) -> None:
        report = run(tmp_path, {"run_eval.py": SCORE_SCRIPT})
        assert report.outcomes[0].status == EntryStatus.EXECUTED
        assert cell(report, "tiny › dev").value == 0.85
        assert "re-running run_eval.py" in report.results.run["note"]


class TestPlanCache:
    def test_a_plan_is_written_and_reused(self, tmp_path) -> None:
        files = {"run_eval.py": SCORE_SCRIPT, "results/scores_tiny.csv": SCORES_CSV}
        cache = tmp_path / "cache"

        first = run(tmp_path / "one", files, plan_cache_dir=cache)
        assert first.plan_path is not None
        assert first.plan_path.exists()

        second = run(tmp_path / "two", files, plan_cache_dir=cache)
        assert second.plan.to_dict()["tables"] == first.plan.to_dict()["tables"]

    def test_refresh_recomputes_it(self, tmp_path) -> None:
        files = {"run_eval.py": SCORE_SCRIPT, "results/scores_tiny.csv": SCORES_CSV}
        cache = tmp_path / "cache"
        run(tmp_path / "one", files, plan_cache_dir=cache)
        report = run(tmp_path / "two", files, plan_cache_dir=cache, refresh_plan=True)
        assert report.plan.tables[0].cells[0].recipe is not None


class TestDifferenceColumns:
    TEX = SCORES_PAPER_TEX.replace("{lcc}", "{lccc}").replace(
        r"Model & dev & test \\", r"Model & dev & test & $\Delta$ \\"
    ).replace(r"tiny  & 0.85 & 0.65 \\", r"tiny  & 0.85 & 0.65 & 0.20 \\").replace(
        r"huge  & 0.91 & 0.72 \\", r"huge  & 0.91 & 0.72 & 0.19 \\"
    )

    def test_a_derived_column_is_computed_and_labelled(self, tmp_path) -> None:
        report = run(
            tmp_path,
            {"run_eval.py": SCORE_SCRIPT, "results/scores_tiny.csv": SCORES_CSV},
            tex=self.TEX,
        )
        derived = cell(report, "tiny › Δ")
        assert derived.value == pytest.approx(0.2)
        assert any("derived as" in caveat for caveat in derived.caveats)

    def test_a_derived_column_fails_with_its_operands_reason(self, tmp_path) -> None:
        report = run(
            tmp_path,
            {"run_eval.py": SCORE_SCRIPT, "results/scores_tiny.csv": SCORES_CSV},
            tex=self.TEX,
        )
        derived = cell(report, "huge › Δ")
        assert derived.failure_code == FailureCode.SCRIPT_NOT_FOUND.value
        assert "huge › dev" in derived.evidence


UNDECLARED_SCRIPT = '''\
import os

import pandas as pd  # this repo never declares pandas anywhere

os.makedirs("results", exist_ok=True)
frame = pd.DataFrame(
    [("tiny", "dev", 0.80), ("tiny", "dev", 0.90),
     ("tiny", "test", 0.60), ("tiny", "test", 0.70)],
    columns=["model", "split", "score"],
)
frame.to_csv("results/scores_tiny.csv", index=False)
print("wrote", len(frame), "rows")
'''

#: The committed artifact holds values that are deliberately WRONG. If a run
#: reports the paper's numbers, the only way it could have got them is by
#: executing the script — falling back to the committed file would produce 0.1.
STALE_SCORES_CSV = (
    "model,split,score\n"
    "tiny,dev,0.10\n"
    "tiny,dev,0.10\n"
    "tiny,test,0.10\n"
    "tiny,test,0.10\n"
)

UNDECLARED_FILES = {
    "run_eval.py": UNDECLARED_SCRIPT,
    "results/scores_tiny.csv": STALE_SCORES_CSV,
    "README.md": "# scores\n\nRun `python run_eval.py`.\n",
}


class TestInstallInferred:
    """Installing packages a repo never declared is opt-in and always disclosed."""

    def test_undeclared_imports_refuse_by_default(self, tmp_path) -> None:
        report = run(tmp_path, UNDECLARED_FILES)
        codes = {b.code for outcome in report.outcomes for b in outcome.blockers}
        assert FailureCode.MISSING_DEPENDENCY in codes

    def test_the_flag_records_what_it_installed(self, tmp_path) -> None:
        report = run(tmp_path, UNDECLARED_FILES, install_inferred=True)
        environment = report.results.run.get("environment", {})
        assert "pandas" in environment.get("inferred", [])

    def test_the_deviation_is_stated_against_the_table(self, tmp_path) -> None:
        report = run(tmp_path, UNDECLARED_FILES, install_inferred=True)
        caveats = " ".join(c for outcome in report.outcomes for c in outcome.caveats)
        # These numbers came out of an environment the paper never specified,
        # and a reader has to be able to see that next to them.
        assert "inferred, not declared" in caveats
        assert "pandas" in caveats

    def test_the_script_actually_runs_and_its_numbers_are_used(self, tmp_path) -> None:
        report = run(tmp_path, UNDECLARED_FILES, install_inferred=True)
        codes = {b.code for outcome in report.outcomes for b in outcome.blockers}
        assert FailureCode.MISSING_DEPENDENCY not in codes

        # The committed file says 0.1; the script computes 0.85. Reading the
        # paper's number back proves the code ran rather than being read.
        dev = cell(report, "tiny › dev")
        assert dev is not None and dev.value == pytest.approx(0.85)

    def test_without_the_flag_the_stale_committed_value_is_all_there_is(self, tmp_path) -> None:
        # The mirror image: refused install means the run cannot correct the
        # stale artifact, and must not pretend otherwise.
        report = run(tmp_path, UNDECLARED_FILES)
        dev = cell(report, "tiny › dev")
        assert dev is not None
        assert dev.value is None or dev.value == pytest.approx(0.10)

    def test_the_install_is_charged_against_the_download_budget(self, tmp_path) -> None:
        # A budget only checked against an estimate is not a budget: real bytes
        # landed on disk and the ledger has to show them.
        report = run(tmp_path, UNDECLARED_FILES, install_inferred=True)
        spent = report.results.run["budget"]["downloaded_mb"]
        assert spent > 0, "installing pandas cost megabytes the ledger never recorded"
