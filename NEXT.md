# Milestone 2 — execution agent

Milestone 1 left the pipeline split cleanly in half. Everything below produces
a `results.json` that satisfies the contract in [docs/SCHEMA.md](docs/SCHEMA.md);
the diff engine and both renderers then work unchanged.

## The seams that were left for this

- **`recheck run` in `src/recheck/cli.py`** already has the final argument
  surface (`--repo`, `--max-hours`, `--max-gpu`, `--out`, `--tolerance`) and
  currently extracts, reports that execution is pending, and exits 3. Replace
  the body between extraction and the exit; do not change the signature.
- **`Results` / `ResultCell` in `src/recheck/schema.py`** are the only types
  the executor needs to emit. `ResultCell` already carries `failure_code` and
  `evidence`, and `to_dict` already enforces value-xor-failure.
- **`FailureCode` in `src/recheck/diff/taxonomy.py`** is the complete vocabulary.
  If the executor needs a code that is not there, add it to the enum *and* to
  `DESCRIPTIONS` *and* to the table in docs/SCHEMA.md — three places, on purpose.
- **`Table.referencing_paragraphs`** is already populated by extraction and is
  currently unused. It exists for the mapper below.
- **`scripts/build_calibration_results.py`** is a working, hand-written stand-in
  for the executor against the garden-path repo. It is the reference for the
  output shape and for how honest `run.note` should be.

## Work

### 1. Repo acquisition (`src/recheck/repo/`, new)

Clone or open `--repo` **read-only**. Copy into the sandbox workdir; never
mutate the source path. Record the resolved commit into `Results.run.commit`.
Refuse to proceed if the working tree is dirty and the path is local — the
report must name a commit someone else can check out.

### 2. Script-to-table mapping (`src/recheck/map/`, new)

Input: `Table.caption`, `Table.referencing_paragraphs`, `Table.column_headers`,
plus the repo's README and script inventory. Output: for each table, a ranked
list of candidate entry points with the argv needed to produce it.

This is the one stage where a model call is appropriate — it is judgment over
prose, not parsing. Keep it behind an interface with a deterministic fallback
(filename and header-token matching) so the whole pipeline never *depends* on
the model being right. Cache the mapping to disk keyed by repo commit so reruns
are cheap and reviewable.

When no candidate clears a confidence floor, emit `SCRIPT_NOT_FOUND` with the
scripts that were considered as evidence. Do not guess.

### 3. Environment setup (`src/recheck/exec/env.py`, new)

Container plus `uv`. Resolve from `requirements.txt` / `pyproject.toml` /
`environment.yml` in that order. Two failures are already in the taxonomy and
must be reported rather than worked around:

- resolver conflict → `ENV_UNRESOLVABLE`, evidence = the resolver's own error
- import present but unpinned and unbuildable → `MISSING_DEPENDENCY`, evidence
  = the module name and the failing build line

Pin what actually got installed into `Results.run` so the report is auditable.

### 4. Budgeted execution (`src/recheck/exec/runner.py`, new)

Enforce `--max-hours` and `--max-gpu` as hard ceilings, checked before each
run and again mid-run. Estimate cost first; if the estimate alone exceeds the
remaining budget, do not start — emit `BUDGET_EXCEEDED` with the estimate and
the ceiling, as `build_calibration_results.py` does today.

Seeds: use the seed the paper specifies. If a script is stochastic and neither
paper nor repo documents a seed, that is `NONDETERMINISTIC_NO_SEED` — run it
anyway, report the value, and let the status carry the caveat. Multi-seed runs
populate `n_seeds` and `uncertainty`, which the diff engine already consumes.

Never let a target repo's code escape the sandbox. Everything in CLAUDE.md's
read-only rule applies here.

### 5. Result assembly

Map each produced number back to its cell address. Any paper cell with no
candidate script becomes a failure entry, **not** an omission — omission shows
up as `NOT_ATTEMPTED`, which is a weaker and less useful claim than a specific
code.

## Definition of done

- `recheck run <arxiv-id> --repo <url> --max-gpu 1` produces a report end to
  end with no hand-written JSON anywhere in the path.
- The garden-path paper reproduces from its real repo, replacing
  `scripts/build_calibration_results.py`.
- Every `UNRUNNABLE` cell carries evidence naming a file, a package, a resolver
  error, or a measured cost. Spot-check that none of them read as generic.
- Budget ceilings are enforced under test, including the estimate-only path.
- `pytest` green, `ruff` clean, golden files unchanged — milestone 2 should not
  perturb extraction at all. If a golden file moves, that is a bug in this work.

## Known gaps carried forward

- **No real LaTeX for the calibration paper.** `garden_path_calibration.tex` is
  a transcription; only a PDF exists locally. Export the source from Overleaf
  and point `recheck extract` at it to close this — until then the paper side
  of calibration is synthetic even though the numbers are real.
- **Figures are out of scope** and stay out of scope for v1.
- **Addresses are content-derived.** Renaming a model or reordering tables
  changes them. If that becomes painful, add a stable id alongside the address
  rather than making addresses opaque — readability is what lets a results
  file be written by hand.
