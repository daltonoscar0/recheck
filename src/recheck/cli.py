"""Command line interface."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from . import __version__
from .diff import (
    ConfigError,
    Status,
    compare,
    parse_tolerance,
    render_markdown,
    render_terminal,
    resolve_policy,
)
from .exec import Budget, ExecutionReport, LocalSandbox, RunOptions, execute
from .map.cache import default_cache_dir
from .paper import FetchError, extract_tables, fetch, find_main_tex, load_local, resolve_inputs
from .repo import RepoError, acquire
from .schema import Paper, Results, SchemaError

app = typer.Typer(
    name="recheck",
    help="Cell-by-cell reproduction reports for ML/NLP papers.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)

RepoOption = Annotated[
    str | None, typer.Option("--repo", help="Git URL or local path of the paper's code.")
]
MaxHoursOption = Annotated[
    float, typer.Option("--max-hours", help="Wall-clock budget for the whole run.")
]
MaxGpuOption = Annotated[
    float, typer.Option("--max-gpu", help="GPU-hour budget for the whole run.")
]
OutOption = Annotated[
    Path | None, typer.Option("--out", "-o", help="Write output here instead of stdout.")
]
ToleranceOption = Annotated[
    str | None,
    typer.Option(
        "--tolerance",
        help="Comparison band: a bare number is relative, or 'rel=0.05,abs=0.1'.",
    ),
]
ToleranceConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--tolerance-config",
        help="TOML file with per-table tolerance bands. Defaults to the nearest recheck.toml.",
    ),
]
MaxDownloadOption = Annotated[
    float,
    typer.Option(
        "--max-download-mb",
        help="Ceiling on model weights and data a run may download, in megabytes.",
    ),
]
ResultsOutOption = Annotated[
    Path | None, typer.Option("--results-out", help="Write the raw results.json here.")
]
WorkdirOption = Annotated[
    Path | None,
    typer.Option(
        "--workdir",
        help="Keep the sandbox here instead of a temp directory that is deleted afterwards.",
    ),
]
PlanCacheOption = Annotated[
    Path | None,
    typer.Option("--plan-cache", help="Directory for cached script-to-table mappings."),
]
RefreshPlanOption = Annotated[
    bool, typer.Option("--refresh-plan", help="Recompute the mapping, ignoring any cached plan.")
]
InstallInferredOption = Annotated[
    bool,
    typer.Option(
        "--install-inferred",
        help=(
            "Install packages a repo imports but never declares, instead of refusing. "
            "The run then no longer reproduces the paper's stated environment, and the "
            "report says so against every affected table."
        ),
    ),
]
AllowDirtyOption = Annotated[
    bool,
    typer.Option(
        "--allow-dirty",
        help="Run against a local repo with uncommitted changes; the report cannot then name "
        "a commit anyone else can check out.",
    ),
]
CommittedArtifactsOption = Annotated[
    bool,
    typer.Option(
        "--committed-artifacts/--no-committed-artifacts",
        help="When a script cannot be re-run, aggregate the artifacts committed to the repo "
        "instead, saying so in the report. Off means those cells report the blocker.",
    ),
]


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"recheck {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version_callback, is_eager=True)
    ] = False,
) -> None:
    """Recheck reruns a paper's tables and reports what reproduces."""


def _load_source_document(target: str) -> tuple[str, dict]:
    """Resolve a URL, tarball, directory, or single .tex into one flat document."""
    path = Path(target).expanduser()
    if path.exists():
        if path.is_file() and path.suffix.lower() in (".tex", ".ltx"):
            return path.read_text(), {"main_tex": str(path)}
        source = load_local(path)
    else:
        source = fetch(target)

    tex_files = source.tex_files()
    if not tex_files:
        raise FetchError(f"no .tex files found in {target}")
    main_name = find_main_tex(tex_files) or sorted(tex_files)[0]
    document = resolve_inputs(tex_files[main_name], lambda name: _read_member(tex_files, name))
    return document, {"arxiv_id": source.arxiv_id, "main_tex": main_name}


def _read_member(files: dict[str, str], name: str) -> str | None:
    for candidate in (name, f"{name}.tex", f"{name}.ltx"):
        if candidate in files:
            return files[candidate]
    suffix = name.split("/")[-1]
    for key in files:
        if key.endswith(f"/{suffix}") or key.endswith(f"/{suffix}.tex"):
            return files[key]
    return None


def _load_json_model(path: Path, reader, label: str):
    """Read a JSON input, reporting bad input as bad input.

    An unreadable file is a usage error, not a failed comparison: letting the
    exception escape gave a traceback and exit 1, which is the code that means
    "a cell contradicts the paper".
    """
    try:
        return reader(path)
    except FileNotFoundError as exc:
        raise typer.BadParameter(f"{label} not found: {path}") from exc
    except OSError as exc:
        raise typer.BadParameter(f"{label} could not be read ({path}): {exc}") from exc
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{label} is not valid JSON ({path}): {exc}") from exc



@app.command()
def extract(
    source: Annotated[
        str, typer.Argument(help="arXiv URL or ID, or a local tarball, directory, or .tex file.")
    ],
    out: OutOption = None,
) -> None:
    """Extract tables from a paper's LaTeX source into paper.json."""
    try:
        document, source_meta = _load_source_document(source)
    except FetchError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    paper = extract_tables(document, source=source_meta)
    payload = json.dumps(paper.to_dict(), indent=2, ensure_ascii=False)

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n")
        numeric = sum(1 for t in paper.tables for c in t.cells if c.is_numeric)
        console.print(
            f"Extracted [bold]{len(paper.tables)}[/bold] tables "
            f"([bold]{numeric}[/bold] numeric cells) → {out}"
        )
    else:
        sys.stdout.write(payload + "\n")


@app.command("diff")
def diff_command(
    paper_json: Annotated[Path, typer.Argument(help="paper.json from `recheck extract`.")],
    results_json: Annotated[Path, typer.Argument(help="results.json of freshly-run numbers.")],
    tolerance: ToleranceOption = None,
    tolerance_config: ToleranceConfigOption = None,
    out: OutOption = None,
    markdown: Annotated[
        bool, typer.Option("--markdown/--terminal", help="Render markdown instead of a table.")
    ] = False,
) -> None:
    """Compare extracted paper cells against a results file."""
    try:
        paper = _load_json_model(paper_json, Paper.read, "paper.json")
        results = _load_json_model(results_json, Results.read, "results.json")
        policy, config_used = resolve_policy(
            tolerance_config,
            parse_tolerance(tolerance) if tolerance else None,
            search_from=paper_json,
        )
    except typer.BadParameter as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except (SchemaError, ConfigError, ValueError) as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    if config_used is not None and policy.per_table:
        console.print(
            f"[dim]Using per-table tolerances from {config_used} "
            f"({len(policy.per_table)} table{'s' if len(policy.per_table) > 1 else ''})[/dim]"
        )

    report = compare(paper, results, policy)

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(report))
        console.print(f"Report written → {out}")
    if markdown and out is None:
        sys.stdout.write(render_markdown(report))
    elif not markdown:
        render_terminal(report, console)

    counts = report.counts()
    if counts[Status.RED]:
        raise typer.Exit(code=1)


@app.command()
def run(
    source: Annotated[str, typer.Argument(help="arXiv URL or ID of the paper.")],
    repo: RepoOption = None,
    max_hours: MaxHoursOption = 2.0,
    max_gpu: MaxGpuOption = 1.0,
    max_download_mb: MaxDownloadOption = 512.0,
    tolerance: ToleranceOption = None,
    tolerance_config: ToleranceConfigOption = None,
    out: OutOption = None,
    results_out: ResultsOutOption = None,
    workdir: WorkdirOption = None,
    plan_cache: PlanCacheOption = None,
    refresh_plan: RefreshPlanOption = False,
    install_inferred: InstallInferredOption = False,
    allow_dirty: AllowDirtyOption = False,
    committed_artifacts: CommittedArtifactsOption = True,
) -> None:
    """Run the full pipeline: extract the paper, reproduce it, and diff.

    The repo is copied into a sandbox workdir and never mutated. Budgets are
    hard ceilings: a run whose estimate alone breaches one is refused before it
    starts, and a run that breaches one mid-flight is killed.
    """
    if repo is None:
        err_console.print(
            "[red]error:[/red] --repo is required; recheck cannot reproduce a paper without "
            "the code that produced it. Use `recheck extract` for the paper side alone."
        )
        raise typer.Exit(code=2)

    try:
        document, source_meta = _load_source_document(source)
    except FetchError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    paper = extract_tables(document, source=source_meta)
    numeric = sum(1 for t in paper.tables for c in t.cells if c.is_numeric)
    console.print(
        f"Extracted [bold]{len(paper.tables)}[/bold] tables "
        f"([bold]{numeric}[/bold] numeric cells) from {source}."
    )

    with _sandbox_root(workdir) as root:
        try:
            acquired = acquire(repo, root / "repo", allow_dirty=allow_dirty)
        except RepoError as exc:
            err_console.print(f"[red]error:[/red] {exc}")
            raise typer.Exit(code=2) from exc
        console.print(f"Acquired [bold]{repo}[/bold] at commit [bold]{acquired.commit}[/bold].")

        options = RunOptions(
            budget=Budget(
                max_hours=max_hours, max_gpu_hours=max_gpu, max_download_mb=max_download_mb
            ),
            allow_committed_artifacts=committed_artifacts,
            plan_cache_dir=plan_cache or default_cache_dir(),
            refresh_plan=refresh_plan,
            install_inferred=install_inferred,
        )
        report = execute(paper, acquired, LocalSandbox(root), options)

        if results_out is not None:
            results_out.parent.mkdir(parents=True, exist_ok=True)
            results_out.write_text(
                json.dumps(report.results.to_dict(), indent=2, ensure_ascii=False) + "\n"
            )
            console.print(f"Results written → {results_out}")
        if report.plan_path is not None:
            console.print(f"[dim]Mapping cached → {report.plan_path}[/dim]")

        _render_run(report, paper, tolerance, tolerance_config, out)


def _render_run(
    report: ExecutionReport,
    paper: Paper,
    tolerance: str | None,
    tolerance_config: Path | None,
    out: Path | None,
) -> None:
    try:
        policy, _ = resolve_policy(
            tolerance_config, parse_tolerance(tolerance) if tolerance else None
        )
    except (ConfigError, ValueError) as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    diff_report = compare(paper, report.results, policy)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(diff_report))
        console.print(f"Report written → {out}")
    render_terminal(diff_report, console)

    if diff_report.counts()[Status.RED]:
        raise typer.Exit(code=1)


@contextmanager
def _sandbox_root(workdir: Path | None) -> Iterator[Path]:
    """Where the run happens. A temp dir unless the user wants to keep it."""
    if workdir is not None:
        workdir.mkdir(parents=True, exist_ok=True)
        yield workdir
        return
    temporary = tempfile.mkdtemp(prefix="recheck-")
    try:
        yield Path(temporary)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    app()
