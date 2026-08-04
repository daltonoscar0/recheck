"""Route each paper cell to a repo artifact by aligning names.

The idea is narrow and checkable. A cell address is a list of names the paper
chose — `Table 1 › GPT-2-large › Ambiguous`. A repo spells the same names in
its filenames (`surprisal_gpt2-large.csv`) and in the values of its categorical
columns (`condition = ambiguous`). If every name in an address can be accounted
for that way, the cell has a recipe; if any name cannot, the cell gets a
failure naming what was considered.

No model call happens here. That is deliberate: a routing decision that changes
between runs cannot be regression-tested, and the report is only as trustworthy
as the routing under it. `Mapper` is a protocol so a model-backed mapper can be
dropped in later without the pipeline ever depending on it being right.
"""

from __future__ import annotations

from typing import Protocol

from ..diff.taxonomy import FailureCode
from ..schema import Cell, Paper, Table
from .analysis import model_enumerations, names_a_model, produces
from .inventory import RepoInventory, TabularArtifact
from .plan import (
    AggregateRecipe,
    CellPlan,
    DifferenceRecipe,
    EntryPoint,
    Filter,
    PlanFailure,
    RepoPlan,
    Statistic,
    TablePlan,
)
from .tokens import content_tokens, is_difference_marker, normalize, normalized_phrases

#: A candidate entry point scoring below this is not worth naming as one.
CONFIDENCE_FLOOR = 1.0

_STATISTIC_WORDS: tuple[tuple[str, Statistic], ...] = (
    ("median", Statistic.MEDIAN),
    ("total", Statistic.SUM),
    ("sum", Statistic.SUM),
    ("count", Statistic.COUNT),
    ("mean", Statistic.MEAN),
    ("average", Statistic.MEAN),
)


class Mapper(Protocol):
    """Turns a paper plus a repo inventory into a plan."""

    name: str

    def map_paper(self, paper: Paper, inventory: RepoInventory, commit: str) -> RepoPlan: ...


def table_tokens(table: Table) -> set[str]:
    """The words that describe a table: caption, headers, and citing prose."""
    return content_tokens(
        table.caption,
        *(text for row in table.column_headers for text in row),
        *table.referencing_paragraphs,
    )


class DeterministicMapper:
    """Name-alignment mapping. Same inputs, same plan, every time."""

    name = "deterministic"

    def map_paper(self, paper: Paper, inventory: RepoInventory, commit: str) -> RepoPlan:
        return RepoPlan(
            commit=commit,
            mapper=self.name,
            tables=[self.map_table(table, inventory) for table in paper.tables],
        )

    def map_table(self, table: Table, inventory: RepoInventory) -> TablePlan:
        numeric = [cell for cell in table.cells if cell.is_numeric]
        plan = TablePlan(
            table_id=table.id,
            table_index=table.index,
            considered=[script.path for script in inventory.scripts],
        )
        if not numeric:
            return plan

        wanted = table_tokens(table)
        caption_phrases = normalized_phrases(table.caption)
        # Columns some address already selects a value in are off-limits to
        # caption-derived filtering: a caption that happens to say "ambiguous"
        # must not narrow a table that already varies over condition.
        claimed = _claimed_columns(numeric, inventory)

        resolved: dict[str, AggregateRecipe] = {}
        failures: dict[str, PlanFailure] = {}
        for cell in numeric:
            if _is_difference_cell(cell):
                continue
            recipe, failure = _recipe_for(
                cell, numeric, inventory, wanted, caption_phrases, claimed
            )
            if recipe is not None:
                resolved[cell.address] = recipe
            else:
                assert failure is not None
                failures[cell.address] = failure

        for cell in numeric:
            if _is_difference_cell(cell):
                plan.cells.append(_difference_plan(cell, numeric, resolved, inventory))
            elif cell.address in resolved:
                plan.cells.append(CellPlan(address=cell.address, recipe=resolved[cell.address]))
            else:
                plan.cells.append(CellPlan(address=cell.address, failure=failures[cell.address]))

        plan.candidates = rank_entry_points(plan, inventory, wanted)
        return plan


# --------------------------------------------------------------------------
# difference columns


def _is_difference_cell(cell: Cell) -> bool:
    return bool(cell.col_path) and is_difference_marker(cell.col_path[-1])


def _difference_operands(cell: Cell, numeric: list[Cell]) -> tuple[str, str] | None:
    """The two same-row cells a difference column is the difference of.

    Only fires on an unambiguous row: exactly two non-difference numeric cells
    sharing this cell's row path. Three would mean guessing which pair the paper
    meant, and guessing is how a report starts inventing numbers.
    """
    siblings = [
        other
        for other in numeric
        if other.row_path == cell.row_path and not _is_difference_cell(other)
    ]
    if len(siblings) != 2:
        return None
    siblings.sort(key=lambda c: c.col)
    return siblings[0].address, siblings[1].address


def _difference_plan(
    cell: Cell,
    numeric: list[Cell],
    resolved: dict[str, AggregateRecipe],
    inventory: RepoInventory,
) -> CellPlan:
    operands = _difference_operands(cell, numeric)
    if operands is None:
        return CellPlan(
            address=cell.address,
            failure=PlanFailure(
                code=FailureCode.SCRIPT_NOT_FOUND,
                evidence=(
                    f"column {cell.col_path[-1]!r} reads as a difference, but row "
                    f"{' › '.join(cell.row_path)!r} does not have exactly two other numeric "
                    f"columns to take it over, so the operands are undetermined"
                ),
            ),
        )
    if all(address in resolved for address in operands):
        return CellPlan(
            address=cell.address,
            recipe=DifferenceRecipe(minuend=operands[0], subtrahend=operands[1]),
        )
    missing = [address for address in operands if address not in resolved]
    code, why = _unmatched_code(cell, numeric, inventory)
    return CellPlan(
        address=cell.address,
        failure=PlanFailure(
            code=code,
            evidence=(
                f"derived as ({operands[0]}) − ({operands[1]}), and "
                f"{', '.join(missing)} could not be produced: {why}"
            ),
        ),
    )


# --------------------------------------------------------------------------
# recipes


def _address_names(cell: Cell) -> list[str]:
    return [name for name in (*cell.row_path, *cell.col_path) if name.strip()]


def _claimed_columns(cells: list[Cell], inventory: RepoInventory) -> set[str]:
    claimed: set[str] = set()
    for cell in cells:
        for name in _address_names(cell):
            if is_difference_marker(name):
                continue
            for artifact in inventory.artifacts:
                match = artifact.category_column_for(name)
                if match is not None:
                    claimed.add(match[0])
    return claimed


def _score_artifact(
    artifact: TabularArtifact, names: list[str], wanted: set[str]
) -> tuple[float, dict[str, tuple[str, str]]] | None:
    """Score an artifact for one cell, or None when it cannot account for a name.

    Names matched by the filename need no filter — a per-model CSV carries no
    model column. Names matched by a categorical value become filters. A name
    can be matched both ways, and that counts twice on purpose: a file both
    named for a model *and* carrying a model column is the strongest evidence
    available that it is the right file.
    """
    filters: dict[str, tuple[str, str]] = {}
    by_filename = 0
    by_category = 0
    for name in names:
        match = artifact.category_column_for(name)
        in_filename = artifact.filename_matches(name)
        if match is not None:
            filters[name] = match
            by_category += 1
        if in_filename:
            by_filename += 1
        if match is None and not in_filename:
            return None

    path_tokens = content_tokens(artifact.path)
    from_address = "".join(normalize(name) for name in names)
    extra = {t for t in path_tokens if t not in wanted and normalize(t) not in from_address}
    score = 3.0 * by_filename + 2.0 * by_category + len(path_tokens & wanted) - 0.5 * len(extra)
    return score, filters


def _pick_value_column(
    artifact: TabularArtifact, wanted: set[str], exclude: set[str]
) -> tuple[str | None, list[str]]:
    """Choose the numeric column the table reports. A tie is a failure, not a coin flip."""
    scored: list[tuple[int, str]] = []
    for column in artifact.numeric_columns:
        if column in exclude:
            continue
        overlap = len(content_tokens(column) & wanted)
        if overlap:
            scored.append((overlap, column))
    if not scored:
        return None, [c for c in artifact.numeric_columns if c not in exclude]
    best = max(score for score, _ in scored)
    tied = sorted(column for score, column in scored if score == best)
    return (tied[0], tied) if len(tied) == 1 else (None, tied)


def _pick_statistic(wanted: set[str]) -> Statistic:
    for word, statistic in _STATISTIC_WORDS:
        if word in wanted:
            return statistic
    return Statistic.MEAN


def _recipe_for(
    cell: Cell,
    peers: list[Cell],
    inventory: RepoInventory,
    wanted: set[str],
    caption_phrases: set[str],
    claimed: set[str],
) -> tuple[AggregateRecipe | None, PlanFailure | None]:
    names = _address_names(cell)
    scored = []
    for artifact in inventory.artifacts:
        result = _score_artifact(artifact, names, wanted)
        if result is not None:
            scored.append((result[0], artifact, result[1]))

    if not scored:
        code, evidence = _unmatched_code(cell, peers, inventory)
        return None, PlanFailure(code=code, evidence=evidence)

    scored.sort(key=lambda item: (-item[0], item[1].path))
    _, artifact, name_filters = scored[0]

    filters = [Filter(column=column, value=value) for column, value in name_filters.values()]
    filter_columns = {f.column for f in filters}

    # The caption states the table's scope ("for NP/Z items"); honour it on any
    # categorical column no address varies over.
    for column, values in artifact.categories.items():
        if column in filter_columns or column in claimed:
            continue
        hits = [value for value in values if normalize(value) in caption_phrases]
        if len(hits) == 1:
            filters.append(Filter(column=column, value=hits[0]))
            filter_columns.add(column)

    value_column, candidates = _pick_value_column(artifact, wanted, filter_columns)
    if value_column is None:
        detail = (
            f"columns {', '.join(candidates)} match the caption equally well"
            if len(candidates) > 1
            else f"none of its numeric columns ({', '.join(candidates) or 'there are none'}) "
            f"matches the caption or the column headers"
        )
        return None, PlanFailure(
            code=FailureCode.SCRIPT_NOT_FOUND,
            evidence=(
                f"{artifact.path} aligns to {' › '.join(names)}, but the quantity the table "
                f"reports is ambiguous: {detail}"
            ),
        )

    return (
        AggregateRecipe(
            artifact=artifact.path,
            value_column=value_column,
            statistic=_pick_statistic(wanted),
            filters=tuple(sorted(filters, key=lambda f: f.column)),
            uncertainty=Statistic.STDEV if cell.uncertainty_kind.value != "none" else None,
        ),
        None,
    )


# --------------------------------------------------------------------------
# failures


def _unmatched_code(
    cell: Cell, peers: list[Cell], inventory: RepoInventory
) -> tuple[FailureCode, str]:
    """The most specific code for a cell nothing in the repo produces.

    When a repo enumerates the checkpoints it can score, and the paper's other
    rows in the same position are in that list while this one is not, the repo
    is telling you exactly which model it cannot reach. `MISSING_CHECKPOINT` is
    a stronger and more useful claim than "no script found".
    """
    checkpoint = _missing_checkpoint(cell, peers, inventory)
    if checkpoint is not None:
        return FailureCode.MISSING_CHECKPOINT, checkpoint

    names = _address_names(cell)
    considered = [a.path for a in inventory.artifacts] or [s.path for s in inventory.scripts]
    shown = ", ".join(considered[:6]) + (
        f", and {len(considered) - 6} more" if len(considered) > 6 else ""
    )
    return FailureCode.SCRIPT_NOT_FOUND, (
        f"nothing in the repo produces {' › '.join(names)}: no filename or column value matches "
        f"{', '.join(repr(n) for n in names)} among {shown or 'an empty repo'}"
    )


def _missing_checkpoint(cell: Cell, peers: list[Cell], inventory: RepoInventory) -> str | None:
    enumerations = model_enumerations(inventory.scripts)
    if not enumerations:
        return None
    for index, name in enumerate(cell.row_path):
        siblings = {
            other.row_path[index]
            for other in peers
            if len(other.row_path) > index and other.row_path[index] != name
        }
        for script_path, ids in enumerations:
            if names_a_model(name, ids):
                continue
            if not any(names_a_model(sibling, ids) for sibling in siblings):
                continue
            return (
                f"no checkpoint for {name!r} is reachable from this repo: {script_path} "
                f"enumerates {', '.join(repr(model_id) for model_id in ids)}, which covers the "
                f"table's other rows but not this one"
            )
    return None


# --------------------------------------------------------------------------
# entry points


#: Weight per table word a script's *source* mentions, and the most such
#: evidence can ever contribute. Text overlap is the weakest signal here — it
#: exists so a repo that commits none of its outputs still has somewhere to
#: start, and it is capped so it can never outrank actually writing the file.
TEXT_OVERLAP_WEIGHT = 0.25
MAX_TEXT_OVERLAP = 1.5


def rank_entry_points(
    plan: TablePlan, inventory: RepoInventory, wanted: set[str]
) -> list[EntryPoint]:
    """Rank scripts by how plausibly running one produces this table."""
    needed = plan.artifacts()
    ranked: list[EntryPoint] = []
    for script in inventory.scripts:
        score = 0.0
        reasons: list[str] = []
        for artifact in needed:
            fragment = produces(artifact, script)
            if fragment:
                score += 3.0
                reason = f"writes {fragment!r}"
                if reason not in reasons:
                    reasons.append(reason)

        name_overlap = content_tokens(script.path) & wanted
        if name_overlap:
            score += 0.5 * len(name_overlap)
            reasons.append(f"name shares {', '.join(sorted(name_overlap))} with the caption")

        text_overlap = content_tokens(script.text) & wanted
        if text_overlap:
            score += min(TEXT_OVERLAP_WEIGHT * len(text_overlap), MAX_TEXT_OVERLAP)
            reasons.append(
                f"source mentions {', '.join(sorted(text_overlap)[:4])}"
                + (" and more" if len(text_overlap) > 4 else "")
            )

        if score >= CONFIDENCE_FLOOR:
            ranked.append(
                EntryPoint(
                    script=script.path,
                    argv=tuple(script.argv()),
                    score=round(score, 3),
                    why="; ".join(reasons),
                )
            )
    ranked.sort(key=lambda entry: (-entry.score, entry.script))
    return ranked
