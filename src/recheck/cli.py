"""Command line interface."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from . import __version__
from .diff import (
    Status,
    TolerancePolicy,
    compare,
    parse_tolerance,
    render_markdown,
    render_terminal,
)
from .paper import FetchError, extract_tables, fetch, find_main_tex, load_local, resolve_inputs
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
    out: OutOption = None,
    markdown: Annotated[
        bool, typer.Option("--markdown/--terminal", help="Render markdown instead of a table.")
    ] = False,
) -> None:
    """Compare extracted paper cells against a results file."""
    try:
        paper = Paper.read(paper_json)
        results = Results.read(results_json)
        policy = TolerancePolicy(default=parse_tolerance(tolerance))
    except (SchemaError, ValueError) as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

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
    tolerance: ToleranceOption = None,
    out: OutOption = None,
) -> None:
    """Run the full pipeline: extract, reproduce, and diff.

    The execution half is milestone 2. Extraction runs today so the command is
    useful now and the argument surface is already the final one.
    """
    document, source_meta = _load_source_document(source)
    paper = extract_tables(document, source=source_meta)
    numeric = sum(1 for t in paper.tables for c in t.cells if c.is_numeric)
    console.print(
        f"Extracted [bold]{len(paper.tables)}[/bold] tables "
        f"([bold]{numeric}[/bold] numeric cells) from {source}."
    )
    console.print(
        "[yellow]Execution is not implemented yet (milestone 2).[/yellow] "
        "Budgets accepted but unused: "
        f"--max-hours {max_hours}, --max-gpu {max_gpu}"
        + (f", --repo {repo}" if repo else "")
        + (f", --tolerance {tolerance}" if tolerance else "")
    )
    console.print(
        "Run [bold]recheck extract[/bold] to save paper.json, then "
        "[bold]recheck diff[/bold] against a results file."
    )
    if out is not None:
        console.print(f"[dim]--out {out} will receive the report once execution lands.[/dim]")
    raise typer.Exit(code=3)


if __name__ == "__main__":
    app()
