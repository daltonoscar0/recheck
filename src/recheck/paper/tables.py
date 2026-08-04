"""Deterministic LaTeX table extraction.

Turns table floats into a `Paper` of addressed cells. Every decision here is
rule-based: no model call is involved, so extraction is reproducible and a
golden-file diff is a meaningful regression signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..schema import Cell, Paper, Table, make_address
from .latex import (
    collect_emphasis,
    expand_macros,
    find_command,
    find_matching_brace,
    flatten,
    read_args,
    split_rows,
    split_top_level,
    strip_comments,
)
from .numbers import parse_cell

FLOAT_ENVIRONMENTS = ("table", "table*", "sidewaystable")
TABULAR_ENVIRONMENTS = ("tabular", "tabularx", "tabular*", "longtable", "tabulary")

# Environments taking a width argument before the column specification.
_WIDTH_ARG_ENVIRONMENTS = {"tabularx", "tabular*", "tabulary"}

_RULE_ONLY = re.compile(
    r"^(?:\s|\\(?:toprule|midrule|bottomrule|hline|cmidrule|cline|addlinespace|"
    r"noalign|endhead|endfirsthead|endfoot|lastfoot)\b(?:\([^)]*\))?(?:\{[^}]*\})?"
    r"(?:\[[^\]]*\])?)*$"
)
_MIDRULE = re.compile(r"\\midrule\b|\\hline\b")
_REF = re.compile(r"\\(?:ref|autoref|Cref|cref|tabref|eqref)\b\*?\{([^}]*)\}")


@dataclass
class Environment:
    name: str
    inner: str
    start: int
    end: int


@dataclass
class _Placed:
    raw: str
    colspan: int = 1
    is_carry: bool = False


@dataclass
class _Row:
    cells: dict[int, _Placed] = field(default_factory=dict)
    after_midrule: bool = False


def find_environments(text: str, names: tuple[str, ...]) -> list[Environment]:
    """Locate `\\begin{name}...\\end{name}` blocks, handling same-name nesting."""
    out: list[Environment] = []
    for name in names:
        begin = re.compile(r"\\begin\{" + re.escape(name) + r"\}")
        end_token = "\\end{" + name + "}"
        begin_token = "\\begin{" + name + "}"
        for match in begin.finditer(text):
            depth = 1
            i = match.end()
            while i < len(text):
                next_begin = text.find(begin_token, i)
                next_end = text.find(end_token, i)
                if next_end == -1:
                    break
                if next_begin != -1 and next_begin < next_end:
                    depth += 1
                    i = next_begin + len(begin_token)
                    continue
                depth -= 1
                if depth == 0:
                    out.append(
                        Environment(
                            name=name,
                            inner=text[match.end() : next_end],
                            start=match.start(),
                            end=next_end + len(end_token),
                        )
                    )
                    break
                i = next_end + len(end_token)
    out.sort(key=lambda e: e.start)
    return out


def _strip_tabular_preamble(name: str, inner: str) -> str:
    """Drop the width and column-specification arguments from a tabular body."""
    i = 0
    while i < len(inner) and inner[i] in " \t\n":
        i += 1
    if i < len(inner) and inner[i] == "[":  # positioning argument
        close = inner.find("]", i)
        if close != -1:
            i = close + 1
    count = 2 if name in _WIDTH_ARG_ENVIRONMENTS else 1
    _, after = read_args(inner, i, count)
    return inner[after:]


def _row_is_rule_only(row: str) -> bool:
    return bool(_RULE_ONLY.match(row.strip()))


def _place_row(raw_row: str, carries: dict[int, tuple[int, str]]) -> _Row:
    """Expand one `&`-separated row into a column-indexed mapping.

    Continuation rows of a `\\multirow` carry empty placeholder cells in real
    markup, so an empty cell sitting under an active carry inherits its content
    rather than being treated as a blank.
    """
    row = _Row()
    fields = split_top_level(raw_row, "&")
    col = 0
    for cell_src in fields:
        content = cell_src
        colspan = 1

        multicolumn = find_command(content, "multicolumn", 3)
        if multicolumn is not None:
            try:
                colspan = max(1, int(flatten(multicolumn.args[0]) or "1"))
            except ValueError:
                colspan = 1
            content = multicolumn.args[2]

        rowspan = 1
        multirow = find_command(content, "multirow", 3)
        if multirow is not None:
            try:
                rowspan = max(1, int(flatten(multirow.args[0]) or "1"))
            except ValueError:
                rowspan = 1
            content = multirow.args[2]

        is_carry = False
        if not flatten(content) and col in carries and carries[col][0] > 0:
            content = carries[col][1]
            is_carry = True

        row.cells[col] = _Placed(raw=content, colspan=colspan, is_carry=is_carry)
        if rowspan > 1:
            # Stored at full span; the caller decrements once per row, including
            # this one, so the value survives exactly `rowspan - 1` more rows.
            carries[col] = (rowspan, content)
        col += colspan
    return row


def _parse_grid(body: str) -> list[_Row]:
    rows: list[_Row] = []
    carries: dict[int, tuple[int, str]] = {}
    pending_midrule = False
    for raw_row in split_rows(body):
        if _row_is_rule_only(raw_row):
            if _MIDRULE.search(raw_row):
                pending_midrule = True
            continue
        if _MIDRULE.search(raw_row):
            pending_midrule = True
        row = _place_row(raw_row, carries)
        if not row.cells:
            continue
        row.after_midrule = pending_midrule
        pending_midrule = False
        rows.append(row)
        for col in list(carries):
            remaining, content = carries[col]
            carries[col] = (remaining - 1, content)
            if carries[col][0] <= 0:
                del carries[col]
    return rows


def _split_header_body(rows: list[_Row]) -> tuple[list[_Row], list[_Row]]:
    """Header rows are everything before the first `\\midrule`.

    Falls back to a single header row when a table uses no rules at all, which
    is the convention every table we have seen still follows.
    """
    for i, row in enumerate(rows):
        if row.after_midrule and i > 0:
            return rows[:i], rows[i:]
    if len(rows) > 1:
        return rows[:1], rows[1:]
    return [], rows


def _column_count(rows: list[_Row]) -> int:
    width = 0
    for row in rows:
        for col, placed in row.cells.items():
            width = max(width, col + placed.colspan)
    return width


def _header_paths(header_rows: list[_Row], ncols: int) -> list[list[str]]:
    """Build each column's header path by expanding spans down the header rows."""
    paths: list[list[str]] = [[] for _ in range(ncols)]
    for row in header_rows:
        expanded: list[str | None] = [None] * ncols
        for col, placed in row.cells.items():
            text = flatten(placed.raw)
            for offset in range(placed.colspan):
                if col + offset < ncols:
                    expanded[col + offset] = text or None
        for col in range(ncols):
            value = expanded[col]
            if value and (not paths[col] or paths[col][-1] != value):
                paths[col].append(value)
    return paths


def _stub_width(body_rows: list[_Row], ncols: int) -> int:
    """Number of leading label columns.

    A column is a stub while every body entry in it is non-numeric. Always at
    least one and never all of them, so a table of pure numbers still gets a
    row label from its first column.
    """
    width = 0
    for col in range(ncols - 1):
        entries = [r.cells[col].raw for r in body_rows if col in r.cells]
        if not entries:
            break
        if any(parse_cell(e).is_numeric for e in entries):
            break
        width += 1
    return max(1, width)


def _is_section_row(row: _Row, ncols: int) -> bool:
    if len(row.cells) != 1:
        return False
    (col, placed), = row.cells.items()
    return col == 0 and placed.colspan >= ncols > 1 and bool(flatten(placed.raw))


def _extract_table(env: Environment, index: int, tabular: Environment) -> Table | None:
    body = _strip_tabular_preamble(tabular.name, tabular.inner)
    rows = _parse_grid(body)
    if not rows:
        return None

    ncols = _column_count(rows)
    header_rows, body_rows = _split_header_body(rows)
    column_headers = _header_paths(header_rows, ncols)

    caption_cmd = find_command(env.inner, "caption", 1)
    label_cmd = find_command(env.inner, "label", 1)
    caption = flatten(caption_cmd.args[0]) if caption_cmd else ""
    label = flatten(label_cmd.args[0]).strip() if label_cmd else None

    data_rows = [r for r in body_rows if not _is_section_row(r, ncols)]
    stub = _stub_width(data_rows, ncols) if data_rows else 1

    cells: list[Cell] = []
    used_addresses: dict[str, int] = {}
    section: str | None = None
    row_number = 0
    for row in body_rows:
        if _is_section_row(row, ncols):
            section = flatten(row.cells[0].raw)
            continue
        row_number += 1

        row_labels: list[str] = []
        if section:
            row_labels.append(section)
        for col in range(stub):
            placed = row.cells.get(col)
            if placed is not None:
                text = flatten(placed.raw)
                if text:
                    row_labels.append(text)

        for col in range(stub, ncols):
            placed = row.cells.get(col)
            if placed is None:
                continue
            parsed = parse_cell(placed.raw)
            col_path = column_headers[col] if col < len(column_headers) else []
            if not col_path:
                # An unlabelled column still needs a stable, unique address.
                col_path = [f"column {col + 1}"]

            address = make_address(index, row_labels, col_path)
            seen = used_addresses.get(address, 0)
            used_addresses[address] = seen + 1
            if seen:
                address = f"{address} #{seen + 1}"

            cells.append(
                Cell(
                    address=address,
                    row_path=row_labels,
                    col_path=col_path,
                    row=row_number,
                    col=col,
                    raw=placed.raw.strip(),
                    text=parsed.text,
                    is_numeric=parsed.is_numeric,
                    value=parsed.value,
                    uncertainty=parsed.uncertainty,
                    uncertainty_kind=parsed.uncertainty_kind,
                    unit=parsed.unit,
                    emphasis=collect_emphasis(placed.raw),
                    markers=parsed.markers or [],
                )
            )

    return Table(
        index=index,
        label=label,
        caption=caption,
        environment=tabular.name,
        column_headers=column_headers,
        cells=cells,
    )


def _resolve_refs(text: str, by_label: dict[str, Table]) -> str:
    r"""Rewrite `\ref{tab:x}` to the table's number so context reads as prose."""

    def replace(match: re.Match[str]) -> str:
        labels = [part.strip() for part in match.group(1).split(",")]
        rendered = [
            str(by_label[label].index) if label in by_label else label for label in labels
        ]
        return ", ".join(rendered)

    return _REF.sub(replace, text)


def _attach_context(paper: Paper, document: str) -> None:
    """Attach the paragraphs that cite each table, for milestone 2's mapper."""
    by_label = {t.label: t for t in paper.tables if t.label}
    if not by_label:
        return
    float_spans = [
        (e.start, e.end) for e in find_environments(document, FLOAT_ENVIRONMENTS)
    ]

    for match in _REF.finditer(document):
        for label in match.group(1).split(","):
            table = by_label.get(label.strip())
            if table is None:
                continue
            if any(start <= match.start() < end for start, end in float_spans):
                continue  # a \ref inside the float's own caption is not context
            start = document.rfind("\n\n", 0, match.start())
            start = 0 if start == -1 else start + 2
            end = document.find("\n\n", match.end())
            end = len(document) if end == -1 else end
            paragraph = flatten(_resolve_refs(document[start:end], by_label))
            if paragraph and paragraph not in table.referencing_paragraphs:
                table.referencing_paragraphs.append(paragraph)


def extract_tables(document: str, source: dict | None = None) -> Paper:
    """Extract every table float in a LaTeX document into a `Paper`."""
    document = expand_macros(strip_comments(document))
    tables: list[Table] = []
    consumed: list[tuple[int, int]] = []

    for env in find_environments(document, FLOAT_ENVIRONMENTS):
        inner_tabulars = find_environments(env.inner, TABULAR_ENVIRONMENTS)
        if not inner_tabulars:
            continue
        table = _extract_table(env, len(tables) + 1, inner_tabulars[0])
        if table is not None:
            tables.append(table)
        consumed.append((env.start, env.end))

    # Bare tabulars outside any float still carry numbers worth checking.
    for tabular in find_environments(document, TABULAR_ENVIRONMENTS):
        if any(start <= tabular.start < end for start, end in consumed):
            continue
        env = Environment(name=tabular.name, inner=tabular.inner, start=tabular.start,
                          end=tabular.end)
        table = _extract_table(env, len(tables) + 1, tabular)
        if table is not None:
            tables.append(table)

    paper = Paper(tables=tables, source=source or {})
    _attach_context(paper, document)
    return paper


def resolve_inputs(main_tex: str, read_file) -> str:
    r"""Inline `\input`/`\include` so a multi-file project parses as one document.

    `read_file` maps a LaTeX-style path to source text and returns None when the
    file is absent, which is common for generated tables shipped separately.
    """
    seen: set[str] = set()

    def expand(text: str, depth: int) -> str:
        if depth > 10:
            return text
        for name in ("input", "include"):
            while True:
                cmd = find_command(text, name, 1)
                if cmd is None:
                    break
                target = flatten(cmd.args[0]).strip()
                replacement = ""
                if target and target not in seen:
                    seen.add(target)
                    loaded = read_file(target)
                    if loaded is not None:
                        replacement = expand(strip_comments(loaded), depth + 1)
                text = text[: cmd.start] + replacement + text[cmd.end :]
        return text

    return expand(strip_comments(main_tex), 0)


def find_main_tex(candidates: dict[str, str]) -> str | None:
    """Pick the main file from a source tree by looking for `\\documentclass`."""
    for name, text in sorted(candidates.items()):
        if re.search(r"\\documentclass\b", strip_comments(text)):
            return name
    return None


__all__ = [
    "extract_tables",
    "find_environments",
    "find_main_tex",
    "resolve_inputs",
    "find_matching_brace",
]
