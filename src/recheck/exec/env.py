"""Build the environment a repo's scripts need, or say precisely why not.

Two failures in the taxonomy live here, and both are reported rather than
worked around:

- the resolver finds no satisfying set → `ENV_UNRESOLVABLE`, with the
  resolver's own error as evidence
- a script imports something the repo never pins → `MISSING_DEPENDENCY`, with
  the module, the import line, and the file that should have pinned it

The second is a static check, so it costs nothing and runs before any budget is
committed. It catches the most common reproducibility failure in ML repos by a
wide margin: a `requirements.txt` frozen from a working venv that silently
omits the one package the author installed by hand.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..map.analysis import import_line, imported_modules
from ..map.inventory import RepoInventory, ScriptFile
from .sandbox import CommandResult, Sandbox, have

#: Import name -> the distribution that provides it, where they differ.
IMPORT_ALIASES: dict[str, str] = {
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "attr": "attrs",
    "OpenSSL": "pyopenssl",
    "serial": "pyserial",
    "Crypto": "pycryptodome",
    "torch": "torch",
    "hf_hub": "huggingface-hub",
    "huggingface_hub": "huggingface-hub",
}

#: Modules that are always available and never belong in a requirements file.
ALWAYS_AVAILABLE = frozenset({"__future__", "typing_extensions"})

_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


class EnvKind:
    REQUIREMENTS = "requirements.txt"
    PYPROJECT = "pyproject.toml"
    CONDA = "environment.yml"
    NONE = "none"


@dataclass(frozen=True)
class RequirementSource:
    """Where a repo states its dependencies, and what it states."""

    kind: str
    path: str
    distributions: frozenset[str] = frozenset()

    def describe(self) -> str:
        if self.kind == EnvKind.NONE:
            return "no requirements.txt, pyproject.toml, or environment.yml"
        return f"{self.path} ({len(self.distributions)} pinned distributions)"


@dataclass
class Environment:
    """The environment a run will actually use."""

    source: RequirementSource
    python: str
    installed: list[str] = field(default_factory=list)
    venv: Path | None = None
    note: str = ""
    inferred: list[str] = field(default_factory=list)
    """Distributions installed that the repo never declared. Non-empty means the
    run deviated from the paper's stated environment, and every report that
    renders this must say so."""

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "requirements": self.source.path,
            "requirements_kind": self.source.kind,
            "python": self.python,
            "installed": self.installed,
            "note": self.note,
        }
        if self.inferred:
            out["inferred"] = self.inferred
        return out


def normalize_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirements_names(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-")):
            continue
        match = _REQUIREMENT_NAME.match(stripped)
        if match:
            names.add(normalize_distribution(match.group(1)))
    return names


def _pyproject_names(text: str) -> set[str]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return set()
    project = data.get("project", {})
    entries = list(project.get("dependencies", []) or [])
    for extra in (project.get("optional-dependencies", {}) or {}).values():
        entries.extend(extra)
    names: set[str] = set()
    for entry in entries:
        match = _REQUIREMENT_NAME.match(str(entry))
        if match:
            names.add(normalize_distribution(match.group(1)))
    return names


def _conda_names(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        match = _REQUIREMENT_NAME.match(stripped[2:])
        if match:
            names.add(normalize_distribution(match.group(1)))
    return names


def resolve_requirements(root: Path) -> RequirementSource:
    """Find the repo's dependency declaration, in the documented order."""
    for filename, kind, parse in (
        ("requirements.txt", EnvKind.REQUIREMENTS, _requirements_names),
        ("pyproject.toml", EnvKind.PYPROJECT, _pyproject_names),
        ("environment.yml", EnvKind.CONDA, _conda_names),
        ("environment.yaml", EnvKind.CONDA, _conda_names),
    ):
        path = root / filename
        if path.is_file():
            return RequirementSource(
                kind=kind, path=filename, distributions=frozenset(parse(path.read_text()))
            )
    return RequirementSource(kind=EnvKind.NONE, path="")


def unpinned_imports(
    script: ScriptFile, source: RequirementSource, inventory: RepoInventory
) -> list[tuple[str, int]]:
    """Third-party modules the script imports that the repo never pins.

    Local modules — a sibling `.py` in the repo — are not dependencies, and
    neither is anything in the standard library.
    """
    local = {Path(other.path).stem for other in inventory.scripts}
    local |= {Path(other.path).parts[0] for other in inventory.scripts if "/" in other.path}
    missing: list[tuple[str, int]] = []
    for module, line in imported_modules(script).items():
        if module in sys.stdlib_module_names or module in ALWAYS_AVAILABLE or module in local:
            continue
        distribution = normalize_distribution(IMPORT_ALIASES.get(module, module))
        if distribution in source.distributions:
            continue
        missing.append((module, line))
    return missing


def missing_dependency_evidence(
    script: ScriptFile, source: RequirementSource, missing: list[tuple[str, int]]
) -> str:
    module, line = missing[0]
    where = import_line(script, line)
    others = (
        f" (also {', '.join(m for m, _ in missing[1:])})" if len(missing) > 1 else ""
    )
    stated = source.describe()
    return (
        f"{script.path}:{line} runs `{where}`, but {module!r} is not provided by {stated}{others}"
    )


def build(
    sandbox: Sandbox,
    source: RequirementSource,
    *,
    timeout: float,
    inferred: frozenset[str] = frozenset(),
) -> tuple[Environment, CommandResult | None]:
    """Create the run environment. Returns it, plus the failing install if any.

    With no requirements file there is nothing to resolve, so the sandbox's
    interpreter is used directly and the report says so rather than implying an
    environment was reconstructed.

    `inferred` holds distributions read off the script's imports because the repo
    never declared them. Installing those is opt-in (`--install-inferred`) and is
    a real deviation: the run no longer reproduces the environment the paper
    specified, because the paper specified none. Every note this function writes
    when `inferred` is non-empty says so, so the deviation cannot reach a reader
    as an ordinary successful run.
    """
    python = sys.executable
    if source.kind == EnvKind.NONE and not inferred:
        return (
            Environment(
                source=source,
                python=python,
                note="repo declares no dependencies; ran against the sandbox interpreter",
            ),
            None,
        )

    if not have("uv"):
        return (
            Environment(source=source, python=python, note="uv is not installed; used the "
                        "sandbox interpreter without installing the repo's pins"),
            None,
        )

    inferred_note = (
        f"; plus {len(inferred)} package(s) the repo never declared, inferred from imports: "
        f"{', '.join(sorted(inferred))}"
        if inferred
        else ""
    )

    venv = sandbox.root / "venv"
    created = sandbox.run(["uv", "venv", "--python", python, str(venv)], timeout=timeout)
    if not created.ok:
        return (
            Environment(source=source, python=python,
                        note=f"uv venv failed: {created.tail(3)}"),
            created,
        )

    interpreter = venv / "bin" / "python"
    if source.kind == EnvKind.NONE:
        declared: list[str] = []
    elif source.kind == EnvKind.CONDA:
        # A conda environment.yml cannot be reconstructed with pip: conda and
        # PyPI disagree on names for exactly the packages that matter here
        # (conda's "pytorch" is PyPI's "torch"). Translating them would be a
        # guess, and `uv pip install .` — the old behaviour — installed the repo
        # itself rather than its dependencies, then blamed the resolver.
        declared = []
    elif source.kind == EnvKind.PYPROJECT:
        declared = ["."]
    else:
        declared = ["-r", source.path]

    if source.kind == EnvKind.CONDA and not inferred:
        return (
            Environment(
                source=source,
                python=python,
                note=(
                    f"{source.path} declares dependencies for conda, which cannot be "
                    f"reconstructed with pip (the two disagree on package names); "
                    f"ran against the sandbox interpreter"
                ),
            ),
            None,
        )

    install_argv = ["uv", "pip", "install", "--python", str(interpreter), *declared,
                    *sorted(inferred)]
    install = sandbox.run(install_argv, timeout=timeout)
    if not install.ok:
        return Environment(source=source, python=str(interpreter), venv=venv), install

    frozen = sandbox.run(
        ["uv", "pip", "freeze", "--python", str(interpreter)], timeout=timeout
    )
    return (
        Environment(
            source=source,
            python=str(interpreter),
            venv=venv,
            installed=sorted(frozen.stdout.split()) if frozen.ok else [],
            inferred=sorted(inferred),
            note=(
                f"installed from {source.path} with uv{inferred_note}"
                if source.kind != EnvKind.NONE
                else f"repo declares no dependencies{inferred_note or '; nothing installed'}"
            ),
        ),
        None,
    )


def directory_mb(path: Path | None) -> float:
    """Size of a directory tree in megabytes, for charging what an install cost.

    Measured rather than estimated: package sizes are not knowable ahead of
    time, and a budget that is only ever checked against a guess is not a
    budget.
    """
    if path is None or not path.exists():
        return 0.0
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total / (1024 * 1024)


def install_extra(
    sandbox: Sandbox, environment: Environment, extra: frozenset[str], *, timeout: float
) -> CommandResult | None:
    """Add distributions to an environment that was already built.

    The environment is built once and reused across a paper's tables, so a table
    reached later can need a package an earlier one did not. Without this it
    would run against an environment missing its imports and fail at runtime
    with an error that looks like the paper's fault rather than ours.
    """
    wanted = frozenset(extra) - set(environment.inferred)
    if not wanted or environment.venv is None:
        return None
    interpreter = environment.venv / "bin" / "python"
    result = sandbox.run(
        ["uv", "pip", "install", "--python", str(interpreter), *sorted(wanted)],
        timeout=timeout,
    )
    if not result.ok:
        return result
    environment.inferred = sorted(set(environment.inferred) | wanted)
    environment.note += f"; later installed {', '.join(sorted(wanted))}"
    return None


def resolver_evidence(source: RequirementSource, result: CommandResult) -> str:
    """Evidence for `ENV_UNRESOLVABLE`: the resolver's own words."""
    return f"installing {source.path} failed: {result.tail(6) or 'no output from the resolver'}"
