# Milestone 3 — two public papers, and launch

Milestone 2 closed the loop. `recheck run <paper> --repo <url>` acquires the
repo read-only, maps tables to scripts, enforces three ceilings, runs what
fits, and grades every cell. There is no hand-written JSON anywhere in that
path, and the garden-path calibration reproduces from the real repo —
`scripts/build_calibration_results.py` now drives the executor instead of
knowing the repo's CSV layout, and its output's values are byte-identical to
the hand-written ones it replaced.

What is left is breadth and polish: prove it on papers nobody here wrote, make
the sandbox defensible for code nobody here has read, and make the report
something you would put in a README.

## The seams that were left for this

- **`Mapper` in `src/recheck/map/deterministic.py`** is a protocol with one
  implementation. A model-backed mapper implements `map_paper` and is selected
  where `RunOptions.mapper` is set; `map/cache.py` already keys plans by
  `(commit, mapper.name)`, so both can coexist on disk.
- **`Sandbox` in `src/recheck/exec/sandbox.py`** is a protocol.
  `LocalSandbox` isolates by process — scrubbed environment, redirected caches,
  CPU rlimit. A `DockerSandbox` implementing the same three members
  (`root`, `workdir`, `run`) drops in with no runner changes. `have("docker")`
  is already there.
- **`classify` in `src/recheck/exec/runner.py`** turns a failed run's output
  into a code. It is a flat list of markers; new papers will add markers, and
  each one wants a test in `TestClassify`.
- **`KNOWN_WEIGHTS_MB` in `src/recheck/exec/estimate.py`** is a small table
  plus a parameter-count regex. Wrong sizes cause wrong refusals, so widen it
  as real papers turn up checkpoints it does not know.
- **`RepoInventory.artifacts` only holds CSV/TSV.** Papers whose numbers land
  in JSON, `.jsonl`, or a printed stdout table are not routable yet. The
  extension point is `sniff_artifact` plus `harvest.aggregate`.
- **`AggregateRecipe.statistic`** covers mean/median/sum/count/stdev. Anything
  a paper reports that is not one of those (a fitted coefficient, a p-value)
  needs either a new statistic or an explicit failure.

## Work

### 1. Two public probing/surprisal papers, end to end

Pick two with public LaTeX on arXiv and a public repo. For each, run

```bash
uv run recheck run <arxiv-id> --repo <url> --max-gpu 0 --results-out r.json --out report.md
```

and read the plan cache before believing the numbers. Expect the first run of
each to fail in a way that is recheck's fault, not the paper's; fix the mapper
or the taxonomy, add a test that pins the fix, and rerun.

Record for each paper: what routed, what did not and why, and whether the
failure codes were the *right* ones. A paper where every cell comes back
`SCRIPT_NOT_FOUND` is a mapper bug wearing a taxonomy code.

Add both as fixtures. Extraction fixtures need golden files
(`tests/conftest.py::FIXTURE_NAMES` plus `scripts/update_golden.py`); results
fixtures do not.

### 2. Container sandbox backend (`src/recheck/exec/sandbox.py`)

`LocalSandbox` runs a target repo's code as the invoking user. That is fine for
the calibration repo and not fine for arbitrary repos off arXiv, which is
exactly what launch means. Implement `DockerSandbox` against the existing
protocol: mount the workdir, drop the network by default, cap memory, and map
a non-zero exit from the container itself to `OTHER` with the container's own
error rather than to a silent failure.

Select it with `--sandbox docker|local|auto`, defaulting to `auto`, and put the
chosen backend in `Results.run.sandbox` — the field is already written and
already rendered.

Fall back to `LocalSandbox` with a printed warning when Docker is absent, and
say which backend ran in the report. A report that does not say how isolated it
was is making a claim it has not earned.

### 3. Report polish

The terminal report is readable; the markdown one is what people will paste.
Two things it is missing:

- **A provenance block, not a provenance line.** `run.entry_points` carries
  status, estimate, blockers and caveats per table, and the renderer currently
  collapses all of it into `run.note`. A short table — table, script, status,
  what it cost — would say more than the sentence does.
- **Per-cell provenance.** `run.entry_points` is per table, so a report cannot
  currently mark *which* cells were re-run versus read from committed files.
  This is the schema change milestone 2 deliberately did not make (it would
  have moved the extraction goldens mid-milestone). Do it here: add an optional
  `provenance` field to `ResultCell`, bump `SCHEMA_VERSION` to `1.1`,
  regenerate the goldens in the *same* commit, and document the bump in
  docs/SCHEMA.md.

### 4. README GIF and launch

Record `./scripts/demo.sh` plus one real paper run. The GIF should show a
`BUDGET_EXCEEDED` with its evidence on screen — that is the thing that makes
people understand what this is, and it is the frame worth choosing carefully.

## Definition of done

- Two public papers reproduce end to end from their real repos, with their
  reports committed under `reports/` and their plan caches spot-checked.
- No cell in either report reads as generic. Every `UNRUNNABLE` names a file, a
  package, a resolver error, a checkpoint, or a measured cost.
- `DockerSandbox` passes the same runner tests as `LocalSandbox`, and the
  backend that ran is on the report.
- `pytest -m "not network"` green, `ruff` clean, goldens moved only in the
  commit that deliberately bumps the schema.
- README GIF in place, roadmap marked done.

## Known gaps carried forward

- **No real LaTeX for the calibration paper.** `garden_path_calibration.tex` is
  a transcription; only a PDF exists locally. Export the source from Overleaf
  and point `recheck extract` at it. The paper side of calibration stays
  synthetic until then, even though the numbers are real.
- **`LocalSandbox` is containment, not a security boundary.** See item 2. Until
  it lands, do not point `recheck run` at a repo you have not read.
- **Committed artifacts can stand in for a run.** When a script is refused,
  recheck aggregates the files already in the repo and says so in
  `run.note`. That is a real check — a paper's printed numbers against its own
  committed data — but it is not a re-run, and per-cell provenance (item 3) is
  what will stop a reader having to take the note's word for it.
- **Mapping needs an artifact to exist.** A repo that commits none of its
  outputs is re-mapped after a successful run, but if that run is refused, its
  cells report the blocker rather than a routing failure. That is honest but
  coarse.
- **Only CSV/TSV artifacts are routable**, and only mean/median/sum/count/stdev
  are computable. See the seams above.
- **Addresses are content-derived.** Renaming a model or reordering tables
  changes them. If that becomes painful, add a stable id alongside the address
  rather than making addresses opaque — readability is what lets a results file
  be written by hand.
- **Figures are out of scope** and stay out of scope for v1.
