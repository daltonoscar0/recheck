"""Report renderers: coloured terminal output and a markdown artifact."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table as RichTable
from rich.text import Text

from .engine import CellComparison, DiffReport, Status
from .taxonomy import describe

STATUS_STYLE: dict[Status, str] = {
    Status.GREEN: "bold green",
    Status.YELLOW: "bold yellow",
    Status.RED: "bold red",
    Status.UNRUNNABLE: "bold magenta",
    Status.NOT_ATTEMPTED: "dim",
}

#: One distinct shape per status. Colour alone is not enough: the report is
#: routinely piped to a file, pasted into a message, or read by someone who
#: cannot distinguish red from green, and in every one of those cases three
#: identical dots carry no information at all.
STATUS_MARK: dict[Status, str] = {
    Status.GREEN: "●",
    Status.YELLOW: "◐",
    Status.RED: "✕",
    Status.UNRUNNABLE: "▲",
    Status.NOT_ATTEMPTED: "·",
}

STATUS_BADGE: dict[Status, str] = {
    Status.GREEN: "🟢 green",
    Status.YELLOW: "🟡 yellow",
    Status.RED: "🔴 red",
    Status.UNRUNNABLE: "⬛ unrunnable",
    Status.NOT_ATTEMPTED: "⬜ not attempted",
}


def _short_address(address: str) -> str:
    """Drop the leading `Table N` segment, which the section heading carries."""
    parts = address.split(" › ")
    return " › ".join(parts[1:]) if len(parts) > 1 else address


def _format_value(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"


def _format_delta(comparison: CellComparison) -> str:
    if comparison.delta is None:
        return "—"
    if comparison.relative_delta is None:
        return f"{comparison.delta:+g}"
    return f"{comparison.delta:+g} ({comparison.relative_delta:.1%})"


def _reason(comparison: CellComparison) -> str:
    """Full reason text, used in the markdown artifact."""
    if comparison.failure_code is not None:
        base = f"{comparison.failure_code.value}: {describe(comparison.failure_code)}"
        return f"{base} — {comparison.evidence}" if comparison.evidence else base
    return "; ".join(comparison.notes)


def _short_reason(comparison: CellComparison) -> str:
    """Terminal reason: the code alone, since evidence repeats across cells and
    is printed once in the legend below the tables."""
    if comparison.failure_code is not None:
        return comparison.failure_code.value
    return "; ".join(comparison.notes)


def _print_failure_legend(report: DiffReport, console: Console) -> None:
    """Print each distinct failure reason once, with the cells it accounts for."""
    grouped: dict[tuple[str, str], list[str]] = {}
    for comparison in report.comparisons:
        if comparison.status is not Status.UNRUNNABLE or comparison.failure_code is None:
            continue
        key = (comparison.failure_code.value, comparison.evidence or "")
        grouped.setdefault(key, []).append(_short_address(comparison.address))
    if not grouped:
        return

    console.print("Why cells could not be run", style="bold")
    for (code, evidence), addresses in grouped.items():
        console.print(Text(f"  {code}", style=STATUS_STYLE[Status.UNRUNNABLE]), end="")
        console.print(f"  ({len(addresses)} cell{'s' if len(addresses) > 1 else ''})", style="dim")
        if evidence:
            console.print(f"    {evidence}", style="dim")
        console.print(f"    cells: {', '.join(addresses)}", style="dim")
    console.print()


def render_terminal(report: DiffReport, console: Console | None = None) -> None:
    console = console or Console()
    counts = report.counts()

    inferred = (report.run.get("environment") or {}).get("inferred") or []
    if inferred:
        console.print(
            Text("Environment: inferred, not declared — installed ", style="bold yellow")
            + Text(", ".join(inferred), style="yellow")
            + Text(" (the paper's repo declares them nowhere)", style="dim"),
        )
        console.print()

    if report.run.get("note"):
        console.print(Text(f"Provenance: {report.run['note']}", style="dim"))
        console.print()

    for table_label, comparisons in report.by_table().items():
        grid = RichTable(
            title=table_label,
            title_justify="left",
            header_style="bold",
            expand=False,
        )
        grid.add_column("", width=1)
        grid.add_column("Cell", overflow="fold")
        grid.add_column("Paper", justify="right")
        grid.add_column("Rerun", justify="right")
        grid.add_column("Δ", justify="right")
        grid.add_column("Reason", overflow="fold")

        for comparison in comparisons:
            style = STATUS_STYLE[comparison.status]
            grid.add_row(
                Text(STATUS_MARK[comparison.status], style=style),
                _short_address(comparison.address),
                _format_value(comparison.paper_value),
                _format_value(comparison.result_value),
                _format_delta(comparison),
                Text(_short_reason(comparison), style="dim"),
            )
        console.print(grid)
        console.print()

    _print_failure_legend(report, console)

    summary = Text()
    for status in Status:
        if counts[status]:
            summary.append(f"{STATUS_MARK[status]} {counts[status]} {status.value.lower()}   ",
                           style=STATUS_STYLE[status])
    console.print(summary)

    if report.comparable:
        rate = report.reproduced / report.comparable
        console.print(
            f"Reproduced {report.reproduced}/{report.comparable} comparable cells ({rate:.0%})",
            style="bold",
        )
    if report.unmatched_results:
        console.print(
            f"{len(report.unmatched_results)} result cells matched no paper cell",
            style="yellow",
        )


def render_markdown(report: DiffReport) -> str:
    counts = report.counts()
    lines: list[str] = ["# Reproduction report", ""]

    source = report.paper_source or {}
    if source.get("arxiv_id"):
        lines.append(f"**Paper:** arXiv:{source['arxiv_id']}")
    if source.get("main_tex"):
        lines.append(f"**Source file:** `{source['main_tex']}`")
    if report.run.get("repo"):
        lines.append(f"**Repo:** {report.run['repo']}")
    if report.run.get("commit"):
        lines.append(f"**Commit:** `{report.run['commit']}`")
    # Where the numbers came from is not a footnote. A reader who does not know
    # whether a run happened cannot read the green cells correctly.
    if report.run.get("note"):
        lines.append(f"**Provenance:** {report.run['note']}")
    inferred = (report.run.get("environment") or {}).get("inferred") or []
    if inferred:
        # The run used an environment the paper never specified. That belongs
        # beside the numbers, not buried in the JSON.
        lines.append(
            f"**Environment:** inferred, not declared — installed "
            f"`{'`, `'.join(inferred)}` because the repo declares "
            f"{'them' if len(inferred) > 1 else 'it'} nowhere"
        )
    if lines[-1] != "":
        lines.append("")

    headline = (
        f"**{report.reproduced}/{report.comparable} comparable cells reproduced**"
        if report.comparable
        else "**No comparable cells**"
    )
    lines += [headline, ""]

    lines.append("| Status | Cells |")
    lines.append("| --- | ---: |")
    for status in Status:
        if counts[status]:
            lines.append(f"| {STATUS_BADGE[status]} | {counts[status]} |")
    lines.append("")

    for table_label, comparisons in report.by_table().items():
        lines.append(f"## {table_label}")
        lines.append("")
        lines.append("| | Cell | Paper | Rerun | Δ | Reason |")
        lines.append("| :-: | --- | ---: | ---: | ---: | --- |")
        for comparison in comparisons:
            reason = _reason(comparison).replace("|", "\\|")
            lines.append(
                f"| {STATUS_BADGE[comparison.status].split()[0]} "
                f"| {_short_address(comparison.address)} "
                f"| {_format_value(comparison.paper_value)} "
                f"| {_format_value(comparison.result_value)} "
                f"| {_format_delta(comparison)} "
                f"| {reason} |"
            )
        lines.append("")

    unrunnable = [c for c in report.comparisons if c.status is Status.UNRUNNABLE]
    if unrunnable:
        lines += ["## Why cells could not be run", ""]
        for comparison in unrunnable:
            code = comparison.failure_code.value if comparison.failure_code else "OTHER"
            lines.append(f"- **{_short_address(comparison.address)}** — `{code}`")
            if comparison.evidence:
                lines.append(f"  - {comparison.evidence}")
        lines.append("")

    if report.unmatched_results:
        lines += ["## Result cells with no matching paper cell", ""]
        lines += [f"- `{address}`" for address in report.unmatched_results]
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
