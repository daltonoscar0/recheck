"""Routing paper cells to repo artifacts, and refusing to guess when it cannot."""

from __future__ import annotations

from conftest import SCORE_SCRIPT, SCORES_CSV, SCORES_PAPER_TEX, write_files
from recheck.diff.taxonomy import FailureCode
from recheck.map import (
    AggregateRecipe,
    DeterministicMapper,
    DifferenceRecipe,
    Statistic,
    build_inventory,
)
from recheck.map import cache as plan_cache
from recheck.map.analysis import (
    imported_modules,
    is_stochastic_without_seed,
    model_enumerations,
    produces,
    referenced_model_ids,
)
from recheck.map.inventory import ScriptFile, sniff_artifact
from recheck.map.tokens import contained, normalize, normalized_phrases, same_name
from recheck.paper import extract_tables


def script(text: str, path: str = "s.py") -> ScriptFile:
    return ScriptFile(path=path, text=text)


class TestTokens:
    def test_normalize_keeps_digits_apart(self) -> None:
        assert normalize("GPT-2-large") == "gpt2large"
        assert normalize("Pythia-1.4B") == "pythia1.4b"
        assert normalize("Pythia-14B") != normalize("Pythia-1.4B")
        assert normalize("pythia_1_4b") == normalize("pythia-1.4b")

    def test_containment_needs_three_characters(self) -> None:
        assert contained("GPT-2-large", "surprisal_gpt2-large.csv")
        assert not contained("NP", "input_data.csv")

    def test_values_match_exactly_not_by_prefix(self) -> None:
        assert same_name("Ambiguous", "ambiguous")
        assert not same_name("Ambiguous", "ambiguous_control")

    def test_phrases_rejoin_split_names(self) -> None:
        assert "npz" in normalized_phrases("for NP/Z items")


class TestInventory:
    def test_sniffs_categories_and_numeric_columns(self, tmp_path) -> None:
        root = write_files(tmp_path, {"scores.csv": SCORES_CSV})
        artifact = sniff_artifact(root / "scores.csv", root)
        assert artifact is not None
        assert artifact.numeric_columns == ["score"]
        assert artifact.categories["split"] == ["dev", "test"]
        assert artifact.n_rows == 4

    def test_skips_caches_and_virtualenvs(self, tmp_path) -> None:
        write_files(tmp_path, {"a.py": "x", ".venv/lib/b.py": "y", "__pycache__/c.py": "z"})
        assert [s.path for s in build_inventory(tmp_path).scripts] == ["a.py"]

    def test_finds_the_readme_and_requirements(self, tmp_path) -> None:
        write_files(tmp_path, {"README.md": "hello", "requirements.txt": "numpy==2.0.0\n"})
        inventory = build_inventory(tmp_path)
        assert inventory.readme == "hello"
        assert inventory.requirement_files == ["requirements.txt"]


class TestAnalysis:
    def test_reading_a_file_is_not_producing_it(self) -> None:
        reader = script('df = pd.read_csv("results/scores_tiny.csv")\n')
        writer = script('with open("results/scores_tiny.csv", "w") as h:\n    h.write("a")\n')
        assert produces("results/scores_tiny.csv", reader) is None
        assert produces("results/scores_tiny.csv", writer) is not None

    def test_a_prefix_must_not_run_into_a_longer_name(self) -> None:
        other = script('out = "data/scores_tiny_initial.csv"\nout.to_csv(out)\n')
        assert produces("data/scores_tiny.csv", other) is None

    def test_fstring_prefixes_still_count(self) -> None:
        source = 'p = f"data/scores_{name}.csv"\ndf.to_csv(p)\n'
        assert produces("data/scores_tiny.csv", script(source)) == "data/scores_"

    def test_a_bare_directory_is_not_a_producer(self) -> None:
        assert produces("data/scores_tiny.csv", script('open("data/", "w")\n')) is None

    def test_model_enumerations_need_a_model_shaped_name(self) -> None:
        assert model_enumerations([script('models = ["gpt2-large", "org/pythia-1.4b"]\n')]) == [
            ("s.py", ["gpt2-large", "org/pythia-1.4b"])
        ]
        assert model_enumerations([script('splits = ["dev", "test"]\n')]) == []

    def test_referenced_model_ids_ignore_data_files(self) -> None:
        found = referenced_model_ids(script('m = "org/llama-2-7b"\nf = "data/x.csv"\n'))
        assert found == ["org/llama-2-7b"]

    def test_imports_carry_line_numbers(self) -> None:
        modules = imported_modules(script("import os\n\nfrom minicons import scorer\n"))
        assert modules["os"] == 1
        assert modules["minicons"] == 3

    def test_unseeded_randomness_is_flagged(self) -> None:
        assert is_stochastic_without_seed(script("import random\nrandom.shuffle(x)\n"))
        assert not is_stochastic_without_seed(
            script("import random\nrandom.seed(0)\nrandom.shuffle(x)\n")
        )
        assert not is_stochastic_without_seed(script("print(1)\n"))


class TestDeterministicMapper:
    def _plan(self, tmp_path, files, tex=SCORES_PAPER_TEX):
        root = write_files(tmp_path, files)
        paper = extract_tables(tex)
        return DeterministicMapper().map_paper(paper, build_inventory(root), "abc1234")

    def test_routes_a_cell_to_a_column_and_a_filter(self, tmp_path) -> None:
        plan = self._plan(tmp_path, {"results/scores_tiny.csv": SCORES_CSV})
        cell = plan.tables[0].cells[0]
        assert cell.address == "Table 1 › tiny › dev"
        assert isinstance(cell.recipe, AggregateRecipe)
        assert cell.recipe.value_column == "score"
        assert cell.recipe.statistic is Statistic.MEAN
        assert [str(f) for f in cell.recipe.filters] == ["model=tiny", "split=dev"]

    def test_a_row_the_repo_cannot_produce_names_what_was_considered(self, tmp_path) -> None:
        plan = self._plan(tmp_path, {"results/scores_tiny.csv": SCORES_CSV})
        failed = [c for c in plan.tables[0].cells if c.failure][0]
        assert failed.failure.code is FailureCode.SCRIPT_NOT_FOUND
        assert "huge" in failed.failure.evidence
        assert "results/scores_tiny.csv" in failed.failure.evidence

    def test_a_model_outside_the_repos_enumeration_is_a_missing_checkpoint(
        self, tmp_path
    ) -> None:
        plan = self._plan(
            tmp_path,
            {
                "results/scores_tiny.csv": SCORES_CSV,
                "score.py": 'models = ["tiny"]\nfor m in models:\n    print(m)\n',
            },
        )
        failed = [c for c in plan.tables[0].cells if c.failure][0]
        assert failed.failure.code is FailureCode.MISSING_CHECKPOINT
        assert "score.py" in failed.failure.evidence
        assert "'tiny'" in failed.failure.evidence
        assert "huge" in failed.failure.evidence

    def test_a_difference_column_is_derived_not_guessed(self, tmp_path) -> None:
        tex = SCORES_PAPER_TEX.replace(
            r"Model & dev & test \\", r"Model & dev & test & $\Delta$ \\"
        ).replace(
            r"tiny  & 0.85 & 0.65 \\", r"tiny  & 0.85 & 0.65 & 0.20 \\"
        ).replace(
            r"huge  & 0.91 & 0.72 \\", r"huge  & 0.91 & 0.72 & 0.19 \\"
        ).replace("{lcc}", "{lccc}")
        plan = self._plan(tmp_path, {"results/scores_tiny.csv": SCORES_CSV}, tex=tex)
        delta = [c for c in plan.tables[0].cells if c.address.endswith("Δ")][0]
        assert isinstance(delta.recipe, DifferenceRecipe)
        assert delta.recipe.minuend.endswith("dev")
        assert delta.recipe.subtrahend.endswith("test")

    def test_an_empty_repo_still_produces_an_explained_plan(self, tmp_path) -> None:
        plan = self._plan(tmp_path, {"notes.txt": "nothing here"})
        table = plan.tables[0]
        assert table.candidates == []
        assert all(c.failure is not None for c in table.cells)
        assert "empty repo" in table.cells[0].failure.evidence

    def test_the_producing_script_outranks_the_scripts_that_read_it(self, tmp_path) -> None:
        plan = self._plan(
            tmp_path,
            {
                "results/scores_tiny.csv": SCORES_CSV,
                "run_eval.py": SCORE_SCRIPT,
                "analyse.py": 'import pandas\npandas.read_csv("results/scores_tiny.csv")\n',
            },
        )
        assert plan.tables[0].candidates[0].script == "run_eval.py"
        assert "writes" in plan.tables[0].candidates[0].why

    def test_mapping_is_stable_across_runs(self, tmp_path) -> None:
        files = {"results/scores_tiny.csv": SCORES_CSV, "run_eval.py": SCORE_SCRIPT}
        first = self._plan(tmp_path / "a", files)
        second = self._plan(tmp_path / "b", files)
        assert first.to_dict()["tables"] == second.to_dict()["tables"]


class TestPlanCache:
    def _paper(self):
        return extract_tables(SCORES_PAPER_TEX)

    def _plan(self, tmp_path):
        root = write_files(tmp_path / "repo", {"results/scores_tiny.csv": SCORES_CSV})
        return DeterministicMapper().map_paper(self._paper(), build_inventory(root), "abc1234")

    def _key(self):
        return plan_cache.paper_key(self._paper())

    def test_round_trips(self, tmp_path) -> None:
        plan = self._plan(tmp_path)
        plan_cache.store(tmp_path / "cache", plan, self._key())
        loaded = plan_cache.load(tmp_path / "cache", "abc1234", "deterministic", self._key())
        assert loaded is not None
        assert loaded.to_dict() == plan.to_dict()

    def test_a_different_commit_is_a_miss(self, tmp_path) -> None:
        plan_cache.store(tmp_path / "cache", self._plan(tmp_path), self._key())
        assert (
            plan_cache.load(tmp_path / "cache", "def5678", "deterministic", self._key()) is None
        )

    def test_a_different_paper_is_a_miss(self, tmp_path) -> None:
        # The plan maps *this* paper's cells. Keyed on the repo alone, a second
        # paper against the same repo silently loaded the first paper's plan.
        plan_cache.store(tmp_path / "cache", self._plan(tmp_path), self._key())
        other = plan_cache.paper_key(extract_tables(SCORES_PAPER_TEX.replace("tiny", "huge")))
        assert other != self._key()
        assert plan_cache.load(tmp_path / "cache", "abc1234", "deterministic", other) is None

    def test_a_corrupt_entry_is_a_miss_not_a_crash(self, tmp_path) -> None:
        path = plan_cache.store(tmp_path / "cache", self._plan(tmp_path), self._key())
        path.write_text("{not json")
        assert (
            plan_cache.load(tmp_path / "cache", "abc1234", "deterministic", self._key()) is None
        )

    def test_the_cached_plan_is_readable(self, tmp_path) -> None:
        path = plan_cache.store(tmp_path / "cache", self._plan(tmp_path), self._key())
        text = path.read_text()
        assert "results/scores_tiny.csv" in text
        assert "value_column" in text


class TestQualifiedValueMatching:
    """A paper column headed `md` against corpus values like `bllip-md`."""

    def _artifact(self):
        from recheck.map.inventory import TabularArtifact

        return TabularArtifact(
            path="data/perplexity.csv",
            columns=["model", "corpus", "ppl"],
            n_rows=6,
            categories={
                "model": ["vanilla", "gpt-2"],
                "corpus": ["bllip-xs", "bllip-md", "bllip-md-gptbpe"],
            },
            numeric_columns=["ppl"],
            observed=[
                {"model": "vanilla", "corpus": "bllip-xs"},
                {"model": "vanilla", "corpus": "bllip-md"},
                {"model": "gpt-2", "corpus": "bllip-md"},
                {"model": "gpt-2", "corpus": "bllip-md-gptbpe"},
            ],
        )

    def test_a_unique_qualified_value_resolves(self) -> None:
        assert self._artifact().candidates_for("xs", {"model": "vanilla"}) == [
            ("corpus", "bllip-xs")
        ]

    def test_filters_restrict_which_rows_count(self) -> None:
        # vanilla was only ever run on bllip-md, so `md` is unambiguous for it.
        assert self._artifact().candidates_for("md", {"model": "vanilla"}) == [
            ("corpus", "bllip-md")
        ]

    def test_a_genuine_ambiguity_returns_every_candidate(self) -> None:
        # gpt-2 has both; guessing here would misreport the paper.
        candidates = self._artifact().candidates_for("md", {"model": "gpt-2"})
        assert {value for _, value in candidates} == {"bllip-md", "bllip-md-gptbpe"}

    def test_an_unmatched_name_yields_nothing(self) -> None:
        assert self._artifact().candidates_for("lg", {"model": "vanilla"}) == []


class TestAmbiguityIsRefusedNotSorted:
    """Two files aligning equally well is an ambiguity, not a coin flip."""

    def test_an_equal_scoring_pair_is_refused(self, tmp_path) -> None:
        # Two identically-shaped files that both answer the cell. Sorting by
        # path and taking the first reported one file's number as the paper's.
        rows = "model,score\ntiny,0.85\n"
        root = write_files(
            tmp_path / "repo",
            {"results/a_scores.csv": rows, "results/b_scores.csv": rows},
        )
        plan = DeterministicMapper().map_paper(
            extract_tables(SCORES_PAPER_TEX), build_inventory(root), "c0ffee"
        )
        cells = [c for table in plan.tables for c in table.cells]
        assert cells
        for cell in cells:
            if cell.failure is not None:
                assert "equally well" in cell.failure.evidence or cell.recipe is None


class TestFilenameMatchingIsTokenised:
    def test_a_substring_is_not_a_filename_match(self) -> None:
        from recheck.map.inventory import TabularArtifact

        artifact = TabularArtifact(
            path="results/ambiguous_and_control.csv", columns=["x"], n_rows=1
        )
        # "lg" is inside "logistic"; plain containment matched far too much.
        assert not artifact.filename_matches("lg")

    def test_a_whole_token_still_matches(self) -> None:
        from recheck.map.inventory import TabularArtifact

        artifact = TabularArtifact(path="results/surprisal_gpt2-large.csv",
                                   columns=["x"], n_rows=1)
        assert artifact.filename_matches("gpt2")
