"""Run what can be run, and account honestly for everything else.

The order of operations is the point. Static checks come first because they are
free and produce the most specific codes; the budget gate comes next so an
estimate that already breaches a ceiling never spends a second proving it; the
environment is built only for a run that is actually going to happen; and the
run itself is killed at the deadline rather than trusted to finish.

Every numeric cell in the paper leaves here with either a value or a coded,
evidenced reason. Nothing is dropped — an omission would show up downstream as
`NOT_ATTEMPTED`, which is a weaker claim about a paper than a specific code and
would flatter every partial run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..diff.taxonomy import FailureCode
from ..map import (
    AggregateRecipe,
    DeterministicMapper,
    DifferenceRecipe,
    Mapper,
    RepoInventory,
    RepoPlan,
    TablePlan,
    build_inventory,
)
from ..map import cache as plan_cache
from ..map.analysis import accepts_seed_flag, is_stochastic_without_seed
from ..repo import AcquiredRepo
from ..schema import Paper, ResultCell, Results
from . import env as env_module
from .budget import Budget, CostEstimate, Ledger, Stopwatch
from .estimate import estimate as estimate_cost
from .harvest import HarvestError, aggregate, difference
from .sandbox import CommandResult, LocalSandbox, Sandbox


class Provenance:
    EXECUTED = "executed"
    COMMITTED = "committed_artifact"


class EntryStatus:
    EXECUTED = "executed"
    REFUSED = "refused"
    FAILED = "failed"
    NONE = "no_candidate"


@dataclass(frozen=True)
class Blocker:
    code: FailureCode
    evidence: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "evidence": self.evidence}


@dataclass
class RunOptions:
    budget: Budget = field(default_factory=Budget)
    mapper: Mapper = field(default_factory=DeterministicMapper)
    allow_committed_artifacts: bool = True
    plan_cache_dir: Path | None = None
    refresh_plan: bool = False
    install_inferred: bool = False
    """Install packages a repo imports but never declares, instead of refusing.

    Off by default on purpose. Inferring an environment is a guess about what the
    authors ran, and a guess that silently succeeds is exactly what this tool
    exists to prevent — so it is opt-in, and every run that uses it says so."""


@dataclass
class TableOutcome:
    table_id: str
    status: str
    script: str | None = None
    estimate: CostEstimate | None = None
    blockers: list[Blocker] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"table": self.table_id, "status": self.status}
        if self.script:
            out["script"] = self.script
        if self.estimate is not None:
            out["estimate"] = self.estimate.to_dict()
        if self.blockers:
            out["blockers"] = [b.to_dict() for b in self.blockers]
        if self.caveats:
            out["caveats"] = self.caveats
        if self.seconds:
            out["seconds"] = round(self.seconds, 2)
        return out


@dataclass
class ExecutionReport:
    results: Results
    plan: RepoPlan
    outcomes: list[TableOutcome]
    inventory: RepoInventory
    plan_path: Path | None = None


# --------------------------------------------------------------------------


def execute(
    paper: Paper,
    repo: AcquiredRepo,
    sandbox: Sandbox | None = None,
    options: RunOptions | None = None,
) -> ExecutionReport:
    """Reproduce every numeric cell in `paper` from `repo`, within budget."""
    options = options or RunOptions()
    sandbox = sandbox or LocalSandbox(repo.path.parent)
    ledger = Ledger(options.budget)
    inventory = build_inventory(repo.path)

    plan, plan_path = _load_plan(paper, inventory, repo, options)

    environment: env_module.Environment | None = None
    requirements = env_module.resolve_requirements(repo.path)

    cells: list[ResultCell] = []
    outcomes: list[TableOutcome] = []
    provenances: set[str] = set()

    for index, table_plan in enumerate(plan.tables):
        outcome, environment = _run_table(
            table_plan, inventory, repo, sandbox, ledger, requirements, environment, options
        )
        if outcome.status == EntryStatus.EXECUTED and _has_unrouted_cells(table_plan):
            # A repo that commits none of its outputs has nothing for the mapper
            # to align against until something has been run. Now that a run has
            # written files, look again before calling those cells unroutable.
            inventory = build_inventory(repo.path)
            table_plan = _remap(paper, table_plan, inventory, repo, options)
            plan.tables[index] = table_plan
        outcomes.append(outcome)
        cells.extend(_harvest_table(table_plan, repo, outcome, options, provenances))

    if options.plan_cache_dir is not None:
        plan_path = plan_cache.store(options.plan_cache_dir, plan)

    results = Results(
        cells=cells,
        run={
            **repo.to_run_fields(),
            "sandbox": type(sandbox).__name__,
            "mapper": plan.mapper,
            "environment": (environment.to_dict() if environment else
                            {"requirements": requirements.path,
                             "requirements_kind": requirements.kind,
                             "note": "no environment was built; nothing was run"}),
            "budget": ledger.to_dict(),
            "entry_points": [o.to_dict() for o in outcomes],
            "note": _note(outcomes, provenances, repo),
        },
    )
    return ExecutionReport(
        results=results, plan=plan, outcomes=outcomes, inventory=inventory, plan_path=plan_path
    )


def _load_plan(
    paper: Paper, inventory: RepoInventory, repo: AcquiredRepo, options: RunOptions
) -> tuple[RepoPlan, Path | None]:
    directory = options.plan_cache_dir
    if directory is not None and not options.refresh_plan:
        cached = plan_cache.load(directory, repo.commit, options.mapper.name)
        if cached is not None:
            return cached, plan_cache.cache_path(directory, repo.commit, options.mapper.name)
    plan = options.mapper.map_paper(paper, inventory, repo.commit)
    path = plan_cache.store(directory, plan) if directory is not None else None
    return plan, path


def _has_unrouted_cells(table_plan: TablePlan) -> bool:
    return any(cell.recipe is None for cell in table_plan.cells)


def _remap(
    paper: Paper,
    table_plan: TablePlan,
    inventory: RepoInventory,
    repo: AcquiredRepo,
    options: RunOptions,
) -> TablePlan:
    """Re-map one table against a repo that now has freshly written outputs."""
    table = next((t for t in paper.tables if t.id == table_plan.table_id), None)
    if table is None:  # pragma: no cover - plan and paper come from the same run
        return table_plan
    refreshed = options.mapper.map_paper(
        Paper(tables=[table], source=paper.source), inventory, repo.commit
    )
    return refreshed.tables[0] if refreshed.tables else table_plan


# --------------------------------------------------------------------------
# running one table's entry point


def _run_table(
    table_plan: TablePlan,
    inventory: RepoInventory,
    repo: AcquiredRepo,
    sandbox: Sandbox,
    ledger: Ledger,
    requirements: env_module.RequirementSource,
    environment: env_module.Environment | None,
    options: RunOptions,
) -> tuple[TableOutcome, env_module.Environment | None]:
    if not table_plan.cells:
        return TableOutcome(table_id=table_plan.table_id, status=EntryStatus.NONE), environment

    if not table_plan.candidates:
        considered = ", ".join(table_plan.considered[:8]) or "no scripts at all"
        return (
            TableOutcome(
                table_id=table_plan.table_id,
                status=EntryStatus.NONE,
                blockers=[
                    Blocker(
                        FailureCode.SCRIPT_NOT_FOUND,
                        f"no script in the repo clears the confidence floor for "
                        f"{table_plan.table_id}; considered {considered}",
                    )
                ],
            ),
            environment,
        )

    entry = table_plan.candidates[0]
    script = inventory.script(entry.script)
    outcome = TableOutcome(
        table_id=table_plan.table_id, status=EntryStatus.REFUSED, script=entry.script
    )
    if script is None:  # pragma: no cover - the plan came from this inventory
        outcome.blockers.append(
            Blocker(FailureCode.SCRIPT_NOT_FOUND, f"{entry.script} is no longer in the repo")
        )
        return outcome, environment

    # 1. Static checks. Free, and they name the most specific reason available.
    missing = env_module.unpinned_imports(script, requirements, inventory)
    inferred: frozenset[str] = frozenset()
    if missing and not options.install_inferred:
        outcome.blockers.append(
            Blocker(
                FailureCode.MISSING_DEPENDENCY,
                env_module.missing_dependency_evidence(script, requirements, missing),
            )
        )
    elif missing:
        inferred = frozenset(
            env_module.normalize_distribution(env_module.IMPORT_ALIASES.get(module, module))
            for module, _ in missing
        )
        # Loud, per table: these numbers came out of an environment the paper
        # never specified, and a reader has to be able to see that next to them.
        outcome.caveats.append(
            f"environment inferred, not declared: installed "
            f"{', '.join(sorted(inferred))} because {script.path} imports "
            f"{', '.join(module for module, _ in missing)} and {requirements.describe()} "
            f"does not provide {'them' if len(missing) > 1 else 'it'}"
        )

    if is_stochastic_without_seed(script):
        flag = " (it accepts --seed but the paper documents no value)" if accepts_seed_flag(
            script
        ) else ""
        outcome.caveats.append(
            f"{FailureCode.NONDETERMINISTIC_NO_SEED.value}: {entry.script} samples randomness "
            f"and never seeds it{flag}, so these numbers move between runs"
        )

    # 2. The budget gate, before anything is spent.
    outcome.estimate = estimate_cost(script, inventory)
    refusal = ledger.refuse(outcome.estimate, entry.script)
    if refusal:
        outcome.blockers.append(Blocker(FailureCode.BUDGET_EXCEEDED, refusal))

    if outcome.blockers:
        return outcome, environment

    # 3. Build the environment only for a run that is going to happen. It is
    #    built once per run and reused, so a table reached later may need a
    #    package an earlier one did not — top up rather than run without it.
    failed_install: CommandResult | None = None
    with Stopwatch(ledger) as watch:
        if environment is None:
            environment, failed_install = env_module.build(
                sandbox,
                requirements,
                timeout=ledger.remaining_seconds(),
                inferred=inferred,
            )
        elif inferred:
            failed_install = env_module.install_extra(
                sandbox, environment, inferred, timeout=ledger.remaining_seconds()
            )
    outcome.seconds += watch.elapsed
    if failed_install is not None:
        outcome.blockers.append(
            Blocker(
                FailureCode.ENV_UNRESOLVABLE,
                env_module.resolver_evidence(requirements, failed_install),
            )
        )
        return outcome, environment

    # 4. Run it, killed at the deadline.
    argv = [environment.python, *entry.argv[1:]] if entry.argv[0] == "python" else list(entry.argv)
    deadline = ledger.remaining_seconds()
    if deadline <= 0:
        outcome.blockers.append(
            Blocker(
                FailureCode.BUDGET_EXCEEDED,
                f"the --max-hours ceiling of {ledger.budget.max_hours:g} was already spent "
                f"before {entry.script} could start",
            )
        )
        return outcome, environment

    with Stopwatch(ledger) as watch:
        result = sandbox.run(argv, timeout=deadline, cwd=repo.path)
    outcome.seconds += watch.elapsed

    if result.timed_out:
        outcome.blockers.append(
            Blocker(
                FailureCode.BUDGET_EXCEEDED,
                f"{entry.script} was killed after {result.seconds / 3600:.3f} wall-clock hours, "
                f"which exhausted the --max-hours ceiling of {ledger.budget.max_hours:g}",
            )
        )
        return outcome, environment

    if not result.ok:
        outcome.status = EntryStatus.FAILED
        outcome.blockers.append(classify(result, entry.script, requirements))
        return outcome, environment

    outcome.status = EntryStatus.EXECUTED
    return outcome, environment


# --------------------------------------------------------------------------
# reading the failure out of a failed run

_MODULE_NOT_FOUND = re.compile(r"No module named ['\"]([\w.]+)['\"]")
_MISSING_ARG = re.compile(
    r"(?:the following arguments are required|unrecognized arguments|expected one argument)"
    r"[:\s]*([^\n]*)"
)
_GATED = (
    "GatedRepoError", "is a gated repo", "Access to model", "401 Client Error",
    "403 Client Error", "authentication", "You must be authenticated",
)
_NO_CHECKPOINT = (
    "RepositoryNotFoundError", "Repository Not Found", "does not appear to have a file named",
    "is not a local folder and is not a valid model identifier",
)


def classify(result: CommandResult, script: str, source: env_module.RequirementSource) -> Blocker:
    """Read a failed run's output and pick the most specific code it supports."""
    output = f"{result.stderr}\n{result.stdout}"

    module = _MODULE_NOT_FOUND.search(output)
    if module:
        return Blocker(
            FailureCode.MISSING_DEPENDENCY,
            f"{script} exited {result.returncode} with "
            f"ModuleNotFoundError: No module named {module.group(1)!r}; "
            f"{source.describe()} does not provide it",
        )

    for marker in _GATED:
        if marker in output:
            return Blocker(
                FailureCode.GATED_DATASET,
                f"{script} exited {result.returncode} on an access-controlled resource "
                f"({marker}): {result.tail(4)}",
            )

    for marker in _NO_CHECKPOINT:
        if marker in output:
            return Blocker(
                FailureCode.MISSING_CHECKPOINT,
                f"{script} exited {result.returncode} because the checkpoint it needs is not "
                f"published ({marker}): {result.tail(4)}",
            )

    argument = _MISSING_ARG.search(output)
    if argument:
        return Blocker(
            FailureCode.UNDOCUMENTED_FLAG,
            f"{script} exited {result.returncode} needing an argument the paper never "
            f"specifies: {argument.group(0).strip()}",
        )

    return Blocker(
        FailureCode.OTHER,
        f"{script} exited {result.returncode}; last output was: {result.tail(6) or '(silent)'}",
    )


# --------------------------------------------------------------------------
# turning a table's plan and outcome into result cells


def _harvest_table(
    table_plan: TablePlan,
    repo: AcquiredRepo,
    outcome: TableOutcome,
    options: RunOptions,
    provenances: set[str],
) -> list[ResultCell]:
    values: dict[str, float] = {}
    cells: list[ResultCell] = []
    ran = outcome.status == EntryStatus.EXECUTED

    for cell_plan in table_plan.cells:
        if cell_plan.failure is not None:
            cells.append(
                ResultCell(
                    address=cell_plan.address,
                    failure_code=cell_plan.failure.code.value,
                    evidence=cell_plan.failure.evidence,
                )
            )
            continue
        if not isinstance(cell_plan.recipe, AggregateRecipe):
            continue

        recipe = cell_plan.recipe
        path = repo.path / recipe.artifact
        if not path.is_file():
            cells.append(_blocked(cell_plan.address, outcome, recipe.artifact, "was never written"))
            continue
        if not ran and not options.allow_committed_artifacts:
            cells.append(
                _blocked(
                    cell_plan.address, outcome, recipe.artifact,
                    "was not re-run, and --no-committed-artifacts forbids reading the "
                    "copy committed to the repo",
                )
            )
            continue

        try:
            harvested = aggregate(repo.path, recipe)
        except HarvestError as exc:
            cells.append(
                ResultCell(
                    address=cell_plan.address,
                    failure_code=FailureCode.OTHER.value,
                    evidence=str(exc),
                )
            )
            continue

        provenances.add(Provenance.EXECUTED if ran else Provenance.COMMITTED)
        values[cell_plan.address] = harvested.value
        cells.append(
            ResultCell(
                address=cell_plan.address,
                value=harvested.value,
                uncertainty=harvested.uncertainty,
                n_seeds=1,
                caveats=list(outcome.caveats),
            )
        )

    for cell_plan in table_plan.cells:
        if not isinstance(cell_plan.recipe, DifferenceRecipe):
            continue
        recipe = cell_plan.recipe
        if recipe.minuend in values and recipe.subtrahend in values:
            cells.append(
                ResultCell(
                    address=cell_plan.address,
                    value=difference(values[recipe.minuend], values[recipe.subtrahend]),
                    caveats=[f"derived as {recipe.describe()}", *outcome.caveats],
                )
            )
        else:
            missing = [a for a in (recipe.minuend, recipe.subtrahend) if a not in values]
            cells.append(
                ResultCell(
                    address=cell_plan.address,
                    failure_code=_primary(outcome).code.value if outcome.blockers
                    else FailureCode.OTHER.value,
                    evidence=(
                        f"derived as {recipe.describe()}, and {', '.join(missing)} produced "
                        f"no value"
                        + (f": {_primary(outcome).evidence}" if outcome.blockers else "")
                    ),
                )
            )

    order = {plan.address: index for index, plan in enumerate(table_plan.cells)}
    cells.sort(key=lambda cell: order.get(cell.address, len(order)))
    return cells


def _primary(outcome: TableOutcome) -> Blocker:
    return outcome.blockers[0]


def _blocked(address: str, outcome: TableOutcome, artifact: str, why: str) -> ResultCell:
    if outcome.blockers:
        blocker = _primary(outcome)
        return ResultCell(
            address=address,
            failure_code=blocker.code.value,
            evidence=f"{artifact} {why}: {blocker.evidence}",
        )
    return ResultCell(
        address=address,
        failure_code=FailureCode.OTHER.value,
        evidence=(
            f"{artifact} {why}, and {outcome.script or 'the entry point'} reported no reason"
        ),
    )


def _note(outcomes: list[TableOutcome], provenances: set[str], repo: AcquiredRepo) -> str:
    """One sentence a reader can trust about where these numbers came from."""
    parts: list[str] = []
    if Provenance.EXECUTED in provenances:
        ran = ", ".join(sorted({o.script for o in outcomes if o.status == EntryStatus.EXECUTED
                                and o.script}))
        parts.append(f"numbers produced by re-running {ran} in the sandbox")
    if Provenance.COMMITTED in provenances:
        blocked = sorted(
            {
                f"{o.script} ({', '.join(b.code.value for b in o.blockers)})"
                for o in outcomes
                if o.blockers and o.script
            }
        )
        detail = f"; not re-run: {', '.join(blocked)}" if blocked else ""
        parts.append(
            f"numbers aggregated from artifacts committed at {repo.commit}, not re-scored{detail}"
        )
    if not parts:
        parts.append("nothing was reproducible from this repo; see the failure codes below")
    return "; ".join(parts)
