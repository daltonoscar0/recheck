"""What a repo contains, from the mapper's point of view.

Two kinds of file matter. **Scripts** are candidate entry points — something
that could be run to produce a table. **Tabular artifacts** are the CSV/TSV
files a paper's numbers are actually aggregated out of; the harvester reads
them, and their column values are how a cell address like
`Table 1 › GPT-2-large › Ambiguous` gets resolved to a filter.

Nothing here executes anything or reaches the network.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_SUFFIXES = frozenset({".py", ".sh", ".r"})
ARTIFACT_SUFFIXES = frozenset({".csv", ".tsv"})
README_NAMES = ("readme.md", "readme.rst", "readme.txt", "readme")

#: Directories that are build output, caches, or someone else's dependencies.
SKIP_DIRS = frozenset(
    {
        ".git", ".venv", "venv", "env", "__pycache__", "node_modules", ".mypy_cache",
        ".pytest_cache", ".ruff_cache", ".tox", "site-packages", ".idea", ".vscode",
        "build", "dist", ".eggs",
    }
)

#: Reading a multi-gigabyte CSV to sniff its columns is not worth it; anything
#: this large is data, not a results table someone transcribed into a paper.
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_SCRIPT_BYTES = 1024 * 1024

#: Above this many distinct values a column is data, not a category to filter on.
MAX_CATEGORY_CARDINALITY = 64
MAX_OBSERVED_ROWS = 2000

#: Requirement sources, in the resolution order documented in docs/SCHEMA.md.
REQUIREMENT_FILES = ("requirements.txt", "pyproject.toml", "environment.yml", "environment.yaml")


@dataclass(frozen=True)
class ScriptFile:
    """A candidate entry point, with its source held for static analysis."""

    path: str  # repo-relative, forward slashes
    text: str

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def is_python(self) -> bool:
        return self.path.endswith(".py")

    def argv(self) -> list[str]:
        if self.is_python:
            return ["python", self.path]
        if self.path.endswith(".sh"):
            return ["bash", self.path]
        return ["Rscript", self.path]


@dataclass(frozen=True)
class TabularArtifact:
    """A CSV/TSV in the repo, summarised so the mapper can align it to cells."""

    path: str  # repo-relative
    columns: list[str]
    n_rows: int
    categories: dict[str, list[str]] = field(default_factory=dict)
    """Low-cardinality columns and their distinct values, in first-seen order."""
    numeric_columns: list[str] = field(default_factory=list)
    observed: list[dict[str, str]] = field(default_factory=list)
    """Distinct combinations of categorical values actually present in the file."""

    def candidates_for(
        self, name: str, given: dict[str, str], column: str | None = None
    ) -> list[tuple[str, str]]:
        """Values that partially match `name`, among rows consistent with `given`.

        A paper column headed `md` has to be resolved against corpus values like
        `bllip-md` and `bllip-md-gptbpe`. Which one is right depends on the row:
        this paper's GPT-2 was trained on the BPE variants and its LSTM was not.
        Restricting to rows that actually carry the filters already established
        for this cell is what makes that answerable from the data rather than
        guessed from the string.
        """
        from .tokens import partial_name_match

        rows = [
            row
            for row in self.observed
            if all(row.get(col) == value for col, value in given.items())
        ] or self.observed

        out: list[tuple[str, str]] = []
        for candidate_column, values in self.categories.items():
            if candidate_column in given or (column and candidate_column != column):
                continue
            present = {row.get(candidate_column) for row in rows}
            for value in values:
                if value in present and partial_name_match(name, value):
                    pair = (candidate_column, value)
                    if pair not in out:
                        out.append(pair)
        return out

    def category_column_for(self, name: str) -> tuple[str, str] | None:
        """Find `(column, value)` whose value is this name, or None."""
        from .tokens import same_name

        for column, values in self.categories.items():
            for value in values:
                if same_name(name, value):
                    return column, value
        return None

    def filename_matches(self, name: str) -> bool:
        """Does the path name this thing, as a whole token?

        Plain substring containment matched far too much: normalised, "Ambiguous"
        is inside "ambiguous_and_control", and worse, a two-letter name like "lg"
        is inside "logistic". A filename claim has to survive tokenisation.
        """
        from .tokens import content_tokens, normalize

        wanted = normalize(name)
        if not wanted:
            return False
        parts = content_tokens(self.path)
        if wanted in {normalize(part) for part in parts}:
            return True
        # Also accept a name spelled across adjacent path tokens (bllip-xs).
        joined = "".join(sorted(parts))  # order-insensitive guard, cheap
        return wanted in joined and len(wanted) >= 4


@dataclass
class RepoInventory:
    root: Path
    scripts: list[ScriptFile] = field(default_factory=list)
    artifacts: list[TabularArtifact] = field(default_factory=list)
    readme: str = ""
    requirement_files: list[str] = field(default_factory=list)

    def script(self, path: str) -> ScriptFile | None:
        for script in self.scripts:
            if script.path == path:
                return script
        return None

    def artifact(self, path: str) -> TabularArtifact | None:
        for artifact in self.artifacts:
            if artifact.path == path:
                return artifact
        return None


def _looks_numeric(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _delimiter(path: Path) -> str:
    return "\t" if path.suffix.lower() == ".tsv" else ","


def sniff_artifact(path: Path, root: Path) -> TabularArtifact | None:
    """Read a table's header, distinct categorical values, and numeric columns."""
    try:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle, delimiter=_delimiter(path))
            columns = [c for c in (reader.fieldnames or []) if c]
            if not columns:
                return None
            distinct: dict[str, list[str]] = {c: [] for c in columns}
            seen: dict[str, set[str]] = {c: set() for c in columns}
            numeric_ok = dict.fromkeys(columns, True)
            n_rows = 0
            observed: list[dict[str, str]] = []
            observed_seen: set[tuple[tuple[str, str], ...]] = set()
            for row in reader:
                n_rows += 1
                cells = {c: (row.get(c) or "").strip() for c in columns}
                # Keyed on the categorical combination only. Including the
                # measurement columns made every row distinct, so the cap was
                # reached on the raw row count and the tail of a long file was
                # never observed — which is exactly the co-occurrence evidence
                # the mapper uses to resolve a name against qualified values.
                key = tuple(
                    sorted((c, v) for c, v in cells.items() if not _looks_numeric(v) or not v)
                )
                if key not in observed_seen and len(observed_seen) <= MAX_OBSERVED_ROWS:
                    observed_seen.add(key)
                    observed.append(cells)
                for column in columns:
                    raw = (row.get(column) or "").strip()
                    if not raw:
                        continue
                    if numeric_ok[column] and not _looks_numeric(raw):
                        numeric_ok[column] = False
                    if raw not in seen[column] and len(seen[column]) <= MAX_CATEGORY_CARDINALITY:
                        seen[column].add(raw)
                        distinct[column].append(raw)
    except (OSError, UnicodeDecodeError, csv.Error):
        return None

    categories = {
        column: values
        for column, values in distinct.items()
        if 0 < len(values) <= MAX_CATEGORY_CARDINALITY and not numeric_ok[column]
    }
    return TabularArtifact(
        path=str(path.relative_to(root)).replace("\\", "/"),
        columns=columns,
        n_rows=n_rows,
        categories=categories,
        numeric_columns=[c for c in columns if numeric_ok[c]],
        observed=[{c: v for c, v in row.items() if c in categories} for row in observed],
    )


def build_inventory(root: Path) -> RepoInventory:
    """Walk `root` once and record what the mapper needs to reason about."""
    inventory = RepoInventory(root=root)
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts[:-1]):
            continue
        rel = str(relative).replace("\\", "/")
        suffix = path.suffix.lower()
        name = path.name.lower()

        if name in README_NAMES and not inventory.readme:
            inventory.readme = _read_text(path, MAX_SCRIPT_BYTES)
        if path.name in REQUIREMENT_FILES:
            inventory.requirement_files.append(rel)
        if suffix in SCRIPT_SUFFIXES and path.stat().st_size <= MAX_SCRIPT_BYTES:
            inventory.scripts.append(ScriptFile(path=rel, text=_read_text(path, MAX_SCRIPT_BYTES)))
        elif suffix in ARTIFACT_SUFFIXES and path.stat().st_size <= MAX_ARTIFACT_BYTES:
            artifact = sniff_artifact(path, root)
            if artifact is not None:
                inventory.artifacts.append(artifact)
    return inventory


def _read_text(path: Path, limit: int) -> str:
    try:
        return path.read_text(errors="replace")[:limit]
    except OSError:  # pragma: no cover - unreadable file mid-walk
        return ""
