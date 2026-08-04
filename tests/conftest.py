from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"
RESULTS = FIXTURES / "results"

FIXTURE_NAMES = [
    "clean_booktabs",
    "multilevel_uncertainty",
    "messy_labels",
    "garden_path_calibration",
    "demo_report",
]


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def golden_dir() -> Path:
    return GOLDEN


@pytest.fixture(scope="session")
def results_dir() -> Path:
    return RESULTS


def read_fixture(name: str) -> str:
    return (FIXTURES / f"{name}.tex").read_text()


def read_golden(name: str) -> dict:
    return json.loads((GOLDEN / f"{name}.json").read_text())


def cell_at(paper, address: str):
    """Fetch a cell by address, failing loudly when extraction did not produce it."""
    found = paper.cell_by_address(address)
    assert found is not None, f"no cell extracted at address {address!r}"
    return found


# ---------------------------------------------------------------------------
# Synthetic target repos.
#
# Execution tests need a repo, and the calibration repo is neither present in
# CI nor safe to depend on. These build tiny git repos on the fly so every
# failure path can be provoked deliberately rather than waited for.

GIT_IDENTITY = [
    "-c", "user.email=tests@example.invalid",
    "-c", "user.name=recheck tests",
    "-c", "commit.gpgsign=false",
]


def write_files(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def make_git_repo(root: Path, files: dict[str, str], *, commit: bool = True) -> Path:
    """A committed git repo containing exactly `files`."""
    write_files(root, files)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    if commit:
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(root), *GIT_IDENTITY, "commit", "-q", "-m", "fixture"], check=True
        )
    return root


_SHARED_REPOS: dict[tuple[tuple[str, str], ...], Path] = {}


def shared_git_repo(files: dict[str, str]) -> Path:
    """A cached source repo for tests that only ever read it.

    Building one costs three git subprocesses. Acquisition is read-only by
    design, so every test using the same file set can share one source repo —
    which is also a standing check that acquisition really does not mutate it.
    """
    key = tuple(sorted(files.items()))
    existing = _SHARED_REPOS.get(key)
    if existing is not None and existing.exists():
        return existing
    root = Path(tempfile.mkdtemp(prefix="recheck-fixture-"))
    _SHARED_REPOS[key] = make_git_repo(root / "repo", files)
    return _SHARED_REPOS[key]


#: A repo whose outputs are committed, like the calibration repo. Two models,
#: two conditions, one score column.
SCORES_CSV = (
    "model,split,score\n"
    "tiny,dev,0.80\n"
    "tiny,dev,0.90\n"
    "tiny,test,0.60\n"
    "tiny,test,0.70\n"
)

SCORE_SCRIPT = '''\
import csv
import os

os.makedirs("results", exist_ok=True)

ROWS = [
    ("tiny", "dev", 0.80),
    ("tiny", "dev", 0.90),
    ("tiny", "test", 0.60),
    ("tiny", "test", 0.70),
]

with open("results/scores_tiny.csv", "w", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["model", "split", "score"])
    for row in ROWS:
        writer.writerow(row)
print("wrote results/scores_tiny.csv")
'''

SCORES_PAPER_TEX = r"""
\documentclass{article}
\begin{document}
Table~\ref{tab:scores} reports the mean score per split.
\begin{table}
\begin{tabular}{lcc}
\toprule
Model & dev & test \\
\midrule
tiny  & 0.85 & 0.65 \\
huge  & 0.91 & 0.72 \\
\bottomrule
\end{tabular}
\caption{Mean score on the dev and test splits.}
\label{tab:scores}
\end{table}
\end{document}
"""


@pytest.fixture
def scores_repo(tmp_path):
    """A repo that commits its outputs and can also regenerate them."""
    return make_git_repo(
        tmp_path / "source",
        {
            "run_eval.py": SCORE_SCRIPT,
            "results/scores_tiny.csv": SCORES_CSV,
            "README.md": "# scores\n\nRun `python run_eval.py` to produce the score table.\n",
        },
    )


@pytest.fixture
def scores_paper():
    from recheck.paper import extract_tables

    return extract_tables(SCORES_PAPER_TEX)
