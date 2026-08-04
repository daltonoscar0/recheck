"""Budgets, the sandbox, environment resolution, and reading numbers off disk."""

from __future__ import annotations

import time

import pytest

from conftest import SCORES_CSV, write_files
from recheck.diff.taxonomy import FailureCode
from recheck.exec import (
    Budget,
    CostEstimate,
    HarvestError,
    Ledger,
    LocalSandbox,
    aggregate,
    classify,
    difference,
    estimate,
    weight_size_mb,
)
from recheck.exec import env as env_module
from recheck.exec.sandbox import CommandResult
from recheck.map import Filter, RepoInventory, Statistic, build_inventory
from recheck.map.inventory import ScriptFile
from recheck.map.plan import AggregateRecipe


def script(text: str, path: str = "s.py") -> ScriptFile:
    return ScriptFile(path=path, text=text)


class TestLedger:
    def test_an_estimate_within_budget_is_allowed(self) -> None:
        ledger = Ledger(Budget(max_hours=1.0, max_gpu_hours=1.0, max_download_mb=1000))
        assert ledger.refuse(CostEstimate(wall_hours=0.5, download_mb=10), "s.py") is None

    def test_the_estimate_alone_can_refuse_a_run(self) -> None:
        ledger = Ledger(Budget(max_hours=2.0, max_gpu_hours=0.0, max_download_mb=512))
        refusal = ledger.refuse(
            CostEstimate(download_mb=5930, download_basis=("s.py loads gpt2-large (~3.1 GB)",)),
            "s.py",
        )
        assert refusal is not None
        assert "5930" in refusal
        assert "--max-download-mb" in refusal
        assert "512" in refusal
        assert "gpt2-large" in refusal

    def test_a_zero_gpu_ceiling_refuses_any_gpu_work(self) -> None:
        ledger = Ledger(Budget(max_hours=2.0, max_gpu_hours=0.0, max_download_mb=100_000))
        refusal = ledger.refuse(CostEstimate(gpu_hours=0.5, gpu_basis=("s.py selects cuda",)), "s")
        assert refusal is not None
        assert "--max-gpu" in refusal
        assert "cuda" in refusal

    def test_spending_narrows_what_is_left(self) -> None:
        ledger = Ledger(Budget(max_hours=1.0))
        ledger.spend_wall(1800)
        assert ledger.remaining_hours() == pytest.approx(0.5)
        assert ledger.refuse(CostEstimate(wall_hours=0.75), "s.py") is not None
        assert "already spent" in ledger.refusals[-1]

    def test_the_ledger_reports_what_it_spent(self) -> None:
        ledger = Ledger(Budget())
        ledger.spend_wall(360)
        assert ledger.to_dict()["spent_hours"] == pytest.approx(0.1)


class TestEstimate:
    def test_known_families_have_sizes(self) -> None:
        assert weight_size_mb("gpt2-large") == 3130.0
        assert weight_size_mb("EleutherAI/pythia-1.4b") == pytest.approx(2800.0)
        assert weight_size_mb("org/mystery-model") is None

    def test_a_cpu_pinned_script_costs_no_gpu_time(self) -> None:
        source = 'models = ["gpt2-large"]\nscorer(m, device="cpu")\n'
        cost = estimate(script(source), RepoInventory(root=None))  # type: ignore[arg-type]
        assert cost.download_mb == 3130.0
        assert cost.gpu_hours == 0.0
        assert any("pins itself to CPU" in line for line in cost.basis)

    def test_a_cuda_script_is_charged_gpu_time(self) -> None:
        cost = estimate(script('m = "org/llama-2-7b"\nm.to("cuda")\n'), RepoInventory(root=None))  # type: ignore[arg-type]
        assert cost.gpu_hours > 0
        assert any("GPU" in line for line in cost.basis)

    def test_a_script_with_no_models_costs_nothing(self) -> None:
        cost = estimate(script("print(1)\n"), RepoInventory(root=None))  # type: ignore[arg-type]
        assert cost.download_mb == 0.0
        assert cost.wall_hours == 0.0


class TestSandbox:
    def test_runs_a_command_in_the_workdir(self, tmp_path) -> None:
        sandbox = LocalSandbox(tmp_path)
        (sandbox.workdir / "hello.txt").write_text("hi")
        result = sandbox.run(["ls"], timeout=30)
        assert result.ok
        assert "hello.txt" in result.stdout

    def test_a_run_past_the_deadline_is_killed(self, tmp_path) -> None:
        sandbox = LocalSandbox(tmp_path)
        started = time.monotonic()
        result = sandbox.run(["sleep", "30"], timeout=1.0)
        assert result.timed_out
        assert not result.ok
        assert time.monotonic() - started < 15

    def test_the_environment_is_scrubbed(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("RECHECK_SECRET_TOKEN", "hunter2")
        sandbox = LocalSandbox(tmp_path)
        assert "RECHECK_SECRET_TOKEN" not in sandbox.environment()
        assert sandbox.environment()["HOME"] == str(tmp_path / "home")

    def test_caches_are_redirected_inside_the_sandbox(self, tmp_path) -> None:
        environment = LocalSandbox(tmp_path).environment()
        assert environment["HF_HOME"].startswith(str(tmp_path))
        assert environment["XDG_CACHE_HOME"].startswith(str(tmp_path))

    def test_a_missing_binary_is_reported_not_raised(self, tmp_path) -> None:
        result = LocalSandbox(tmp_path).run(["definitely-not-a-binary"], timeout=5)
        assert result.returncode == 127
        assert "could not start" in result.stderr

    def test_large_output_is_clipped(self, tmp_path) -> None:
        sandbox = LocalSandbox(tmp_path)
        script_path = sandbox.workdir / "noisy.py"
        script_path.write_text("print('x' * 200_000)\n")
        result = sandbox.run(["python3", "noisy.py"], timeout=60)
        assert result.ok
        assert len(result.stdout) < 200_000


class TestRequirements:
    def test_requirements_txt_wins(self, tmp_path) -> None:
        write_files(
            tmp_path,
            {"requirements.txt": "numpy==2.0.0\ntorch==2.8.0\n", "pyproject.toml": "[project]\n"},
        )
        source = env_module.resolve_requirements(tmp_path)
        assert source.kind == env_module.EnvKind.REQUIREMENTS
        assert source.distributions == {"numpy", "torch"}

    def test_pyproject_dependencies_are_read(self, tmp_path) -> None:
        write_files(
            tmp_path,
            {"pyproject.toml": '[project]\ndependencies = ["rich>=13", "typer"]\n'},
        )
        assert env_module.resolve_requirements(tmp_path).distributions == {"rich", "typer"}

    def test_conda_environments_are_read(self, tmp_path) -> None:
        write_files(tmp_path, {"environment.yml": "dependencies:\n  - numpy=2.0\n  - pandas\n"})
        assert env_module.resolve_requirements(tmp_path).distributions == {"numpy", "pandas"}

    def test_a_repo_with_nothing_declared(self, tmp_path) -> None:
        source = env_module.resolve_requirements(tmp_path)
        assert source.kind == env_module.EnvKind.NONE
        assert "no requirements.txt" in source.describe()

    def test_an_unpinned_import_is_found_with_its_line(self, tmp_path) -> None:
        root = write_files(
            tmp_path,
            {
                "requirements.txt": "numpy==2.0.0\n",
                "score.py": "import os\nimport numpy\nfrom minicons import scorer\n",
            },
        )
        inventory = build_inventory(root)
        source = env_module.resolve_requirements(root)
        missing = env_module.unpinned_imports(inventory.scripts[0], source, inventory)
        assert missing == [("minicons", 3)]

        evidence = env_module.missing_dependency_evidence(inventory.scripts[0], source, missing)
        assert "score.py:3" in evidence
        assert "from minicons import scorer" in evidence
        assert "requirements.txt" in evidence

    def test_sibling_modules_are_not_dependencies(self, tmp_path) -> None:
        root = write_files(
            tmp_path, {"requirements.txt": "", "main.py": "import helpers\n", "helpers.py": "x=1\n"}
        )
        inventory = build_inventory(root)
        source = env_module.resolve_requirements(root)
        main = next(s for s in inventory.scripts if s.path == "main.py")
        assert env_module.unpinned_imports(main, source, inventory) == []

    def test_import_aliases_are_honoured(self, tmp_path) -> None:
        root = write_files(
            tmp_path, {"requirements.txt": "scikit-learn==1.5\n", "m.py": "import sklearn\n"}
        )
        inventory = build_inventory(root)
        source = env_module.resolve_requirements(root)
        assert env_module.unpinned_imports(inventory.scripts[0], source, inventory) == []


class TestHarvest:
    def _recipe(self, **kwargs) -> AggregateRecipe:
        base = {
            "artifact": "scores.csv",
            "value_column": "score",
            "statistic": Statistic.MEAN,
            "filters": (Filter("split", "dev"),),
        }
        return AggregateRecipe(**{**base, **kwargs})

    def test_mean_with_a_filter(self, tmp_path) -> None:
        write_files(tmp_path, {"scores.csv": SCORES_CSV})
        harvested = aggregate(tmp_path, self._recipe())
        assert harvested.value == 0.85
        assert harvested.n_rows == 2

    def test_standard_deviation_rides_along(self, tmp_path) -> None:
        write_files(tmp_path, {"scores.csv": SCORES_CSV})
        harvested = aggregate(tmp_path, self._recipe(uncertainty=Statistic.STDEV))
        assert harvested.uncertainty == pytest.approx(0.071, abs=0.001)

    def test_other_statistics(self, tmp_path) -> None:
        write_files(tmp_path, {"scores.csv": SCORES_CSV})
        assert aggregate(tmp_path, self._recipe(statistic=Statistic.SUM)).value == 1.7
        assert aggregate(tmp_path, self._recipe(statistic=Statistic.COUNT)).value == 2.0
        assert aggregate(tmp_path, self._recipe(statistic=Statistic.MEDIAN)).value == 0.85

    def test_a_filter_that_selects_nothing_says_so(self, tmp_path) -> None:
        write_files(tmp_path, {"scores.csv": SCORES_CSV})
        with pytest.raises(HarvestError) as caught:
            aggregate(tmp_path, self._recipe(filters=(Filter("split", "vaildation"),)))
        assert "0 numeric rows" in str(caught.value)
        assert "split=vaildation" in str(caught.value)

    def test_a_missing_column_names_the_columns_there_are(self, tmp_path) -> None:
        write_files(tmp_path, {"scores.csv": SCORES_CSV})
        with pytest.raises(HarvestError) as caught:
            aggregate(tmp_path, self._recipe(value_column="bleu"))
        assert "'bleu'" in str(caught.value)
        assert "model, split, score" in str(caught.value)

    def test_a_missing_file_names_the_path(self, tmp_path) -> None:
        with pytest.raises(HarvestError) as caught:
            aggregate(tmp_path, self._recipe())
        assert "scores.csv" in str(caught.value)

    def test_difference_rounds_like_a_value(self) -> None:
        assert difference(16.879, 10.765) == 6.114


class TestClassify:
    def _result(self, stderr: str, code: int = 1) -> CommandResult:
        return CommandResult(argv=("python", "s.py"), returncode=code, stdout="",
                             stderr=stderr, seconds=1.0)

    def _source(self) -> env_module.RequirementSource:
        return env_module.RequirementSource(
            kind=env_module.EnvKind.REQUIREMENTS,
            path="requirements.txt",
            distributions=frozenset({"numpy"}),
        )

    def test_module_not_found_is_a_missing_dependency(self) -> None:
        blocker = classify(
            self._result("ModuleNotFoundError: No module named 'minicons'"), "s.py", self._source()
        )
        assert blocker.code is FailureCode.MISSING_DEPENDENCY
        assert "minicons" in blocker.evidence
        assert "requirements.txt" in blocker.evidence

    def test_a_gated_repo_is_a_gated_dataset(self) -> None:
        blocker = classify(
            self._result("huggingface_hub.errors.GatedRepoError: 403"), "s.py", self._source()
        )
        assert blocker.code is FailureCode.GATED_DATASET
        assert "GatedRepoError" in blocker.evidence

    def test_an_unpublished_checkpoint(self) -> None:
        blocker = classify(
            self._result("OSError: org/private is not a local folder and is not a valid model "
                         "identifier"),
            "s.py",
            self._source(),
        )
        assert blocker.code is FailureCode.MISSING_CHECKPOINT
        assert "org/private" in blocker.evidence

    def test_a_required_argument_is_an_undocumented_flag(self) -> None:
        blocker = classify(
            self._result("error: the following arguments are required: --checkpoint"),
            "s.py",
            self._source(),
        )
        assert blocker.code is FailureCode.UNDOCUMENTED_FLAG
        assert "--checkpoint" in blocker.evidence

    def test_anything_else_falls_back_to_other_with_the_output(self) -> None:
        blocker = classify(self._result("ZeroDivisionError: division by zero", 3), "s.py",
                           self._source())
        assert blocker.code is FailureCode.OTHER
        assert "ZeroDivisionError" in blocker.evidence
        assert "exited 3" in blocker.evidence

    def test_a_silent_failure_still_carries_evidence(self) -> None:
        blocker = classify(self._result("", 9), "s.py", self._source())
        assert blocker.code is FailureCode.OTHER
        assert blocker.evidence.strip()
        assert "s.py" in blocker.evidence


class TestCondaEnvironments:
    """environment.yml cannot be rebuilt with pip, and pretending otherwise
    installed the repo instead of its dependencies."""

    def test_conda_repo_does_not_pip_install_the_repo_itself(self, tmp_path) -> None:
        from recheck.exec import env as env_module
        from recheck.exec.sandbox import LocalSandbox

        sandbox = LocalSandbox(tmp_path)
        (sandbox.workdir / "environment.yml").write_text(
            "name: x\ndependencies:\n  - python=3.11\n  - pytorch\n"
        )
        source = env_module.resolve_requirements(sandbox.workdir)
        assert source.kind == env_module.EnvKind.CONDA

        environment, failure = env_module.build(sandbox, source, timeout=60)
        assert failure is None
        assert "conda" in environment.note
        assert environment.venv is None

    def test_the_note_says_why_rather_than_blaming_the_resolver(self, tmp_path) -> None:
        from recheck.exec import env as env_module
        from recheck.exec.sandbox import LocalSandbox

        sandbox = LocalSandbox(tmp_path)
        (sandbox.workdir / "environment.yml").write_text("name: x\ndependencies:\n  - numpy\n")
        source = env_module.resolve_requirements(sandbox.workdir)
        environment, _ = env_module.build(sandbox, source, timeout=60)
        assert "disagree on package names" in environment.note
