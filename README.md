# Recheck

**Cell-by-cell reproduction reports for ML/NLP papers.**

[![CI](https://github.com/daltonoscar0/recheck/actions/workflows/ci.yml/badge.svg)](https://github.com/daltonoscar0/recheck/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Point Recheck at a paper and its code. It pulls the tables out of the paper's
LaTeX source, reruns what the repo can actually run, and puts the two side by
side — green, yellow, red — with a specific, evidenced reason for every cell
that could not be run.

The reasons are the point. "Failed" is worthless. `MISSING_CHECKPOINT —
openai/davinci is API-gated and no local checkpoint is published` tells you
something true about the paper.

---

## Status: milestone 2

| | |
| --- | --- |
| Table extraction from LaTeX | ✅ working |
| Diff engine + failure taxonomy | ✅ working |
| Terminal and markdown reports | ✅ working |
| arXiv source fetching | ✅ written, network test marked |
| Repo acquisition, read-only + pinned | ✅ working |
| Script-to-table mapping | ✅ working, deterministic |
| Sandboxed execution, budgets | ✅ working |
| Building an environment a repo never declared | ✅ working, opt-in |
| Container sandbox backend | ⬜ milestone 3 — see [NEXT.md](NEXT.md) |
| Two public papers, launch | ⬜ milestone 3 |

`recheck run` now does the whole thing: pulls the paper's LaTeX, copies the
repo read-only, works out which script produces which table, runs what fits in
the budget, and grades every cell. Nothing in that path is hand-written JSON.

---

## Install

```bash
git clone https://github.com/daltonoscar0/recheck
cd recheck
uv venv --python 3.11
uv pip install -e ".[dev]"
uv run recheck --help
```

## Quickstart

```bash
# The whole pipeline: extract, reproduce, diff
uv run recheck run 2401.12345 --repo https://github.com/example/paper-code \
    --max-hours 2 --max-gpu 0 --out report.md

# Paper side alone: arXiv ID, URL, tarball, directory, or a single .tex file
uv run recheck extract 2401.12345 --out paper.json

# Diff a results file you produced yourself
uv run recheck diff paper.json results.json --out report.md
```

See the whole thing on real fixtures:

```bash
./scripts/demo.sh
```

Exit codes: `0` clean, `1` at least one RED cell, `2` bad input. (`3` is
reserved; it used to mean "stage not implemented".) Usable in CI without
parsing output.

---

## Running a paper

```bash
uv run recheck run <paper> --repo <url-or-path> [options]
```

`<paper>` is an arXiv ID or URL, a tarball, a directory, or a `.tex` file.
`--repo` is required — reproducing a paper without its code is not a thing
recheck can do.

| Flag | Default | What it does |
| --- | --- | --- |
| `--max-hours` | `2.0` | Wall-clock ceiling for the whole run |
| `--max-gpu` | `1.0` | GPU-hour ceiling |
| `--max-download-mb` | `512` | Ceiling on model weights a run may pull |
| `--out` | — | Write the markdown report here |
| `--results-out` | — | Write the raw `results.json` here |
| `--workdir` | temp dir | Keep the sandbox instead of deleting it |
| `--plan-cache` | `~/.cache/recheck/plans` | Where mappings are cached |
| `--refresh-plan` | off | Recompute the mapping, ignoring the cache |
| `--allow-dirty` | off | Run against a repo with uncommitted changes |
| `--no-committed-artifacts` | off | Refuse to fall back to files already in the repo |
| `--tolerance`, `--tolerance-config` | | As for `recheck diff` |

**The repo is never mutated.** It is exported at `HEAD` with `git archive` into
a sandbox workdir — tracked files only, so a repo's `.venv` and `.git` stay
put — and every write happens in the copy. A dirty local tree is refused by
default, because a report that cannot name a commit you could check out is not
worth much.

**Budgets are ceilings, not suggestions.** A run whose *estimate alone* breaches
one is refused before it starts, with the estimate and the ceiling as evidence.
A run that breaches one mid-flight is killed at the deadline.

```
BUDGET_EXCEEDED — score_surprisal_multimodel.py needs an estimated 5930 MB of
downloads against a --max-download-mb ceiling of 512: score_surprisal_multimodel.py
loads gpt2-large (~3.1 GB of weights); … loads EleutherAI/pythia-1.4b (~2.7 GB)
```

**Every cell comes back with a value or a coded reason.** Nothing is dropped —
an omission would surface as `NOT_ATTEMPTED`, which is a weaker claim about a
paper than a specific code.

### How a table is matched to a script

A cell address is a list of names the paper chose. A repo spells the same names
in its filenames and in its categorical column values. When every name in an
address is accounted for, the cell gets a recipe:

```
mean(critical_region_surprisal) over phase1_stimuli/surprisal_gpt2-large.csv
  where condition=ambiguous, construction=NPZ, model=gpt2-large
```

That recipe is written to `~/.cache/recheck/plans/<commit>.deterministic.json`
and is meant to be read. When a number looks wrong, "you took the mean of the
wrong column" is a fixable bug report; "the number is wrong" is not.

There is no model call in this path either. Routing is what everything
downstream trusts, and a stage that changed its mind between runs would make
every diff advisory. When no candidate clears the confidence floor, the cells
get `SCRIPT_NOT_FOUND` naming the scripts that were weighed — recheck does not
guess.

---

## What a report looks like

<!-- generated by `recheck diff --markdown` on tests/fixtures/demo_report.tex -->

**Provenance:** hand-written fixture: every diff status is represented on purpose

**5/9 comparable cells reproduced**

| Status | Cells |
| --- | ---: |
| 🟢 green | 5 |
| 🟡 yellow | 2 |
| 🔴 red | 2 |
| ⬛ unrunnable | 3 |
| ⬜ not attempted | 3 |

| | Cell | Paper | Rerun | Δ | Reason |
| :-: | --- | ---: | ---: | ---: | --- |
| 🟢 | GPT-2-small › NP/Z | 0.94 | 0.95 | +0.01 (1.1%) | |
| 🟡 | GPT-2-small › NP/S | 0.61 | 0.68 | +0.07 (11.5%) | |
| 🔴 | GPT-2-small › Overall | 0.78 | 0.52 | -0.26 (33.3%) | |
| 🟢 | GPT-2-large › NP/Z | 1.42 | 1.55 | +0.13 (9.2%) | within paper's reported ±0.21 |
| 🟢 | GPT-2-large › Overall | 1.15 | 1.17 | +0.02 (1.7%) | |
| 🔴 | Pythia-1.4B › NP/S | 0.79 | -0.05 | -0.84 (106.3%) | sign differs from the paper |
| 🟡 | Pythia-1.4B › Overall | 0.99 | 1.06 | +0.07 (7.1%) | |
| ⬛ | Llama-2-7B › NP/Z | 1.63 | — | — | `ENV_UNRESOLVABLE` |
| ⬛ | Llama-2-7B › Overall | 1.33 | — | — | `BUDGET_EXCEEDED` |
| ⬜ | GPT-4 › NP/Z | 1.80 | — | — | no entry for this cell in the results file |

**Why cells could not be run**

- **Llama-2-7B › NP/Z** — `ENV_UNRESOLVABLE`
  - requirements.txt pins torch==2.0.1 and transformers==4.51.0; transformers 4.51 requires torch>=2.1, so no resolution exists
- **Llama-2-7B › Overall** — `BUDGET_EXCEEDED`
  - scoring 144 items on Llama-2-7B needed an estimated 3.2 GPU-hours against a `--max-gpu` of 1.0

Note the two statuses people usually collapse. `⬛ unrunnable` means we tried
and it cannot run. `⬜ not attempted` means the results file never covered that
cell. Reporting them as one number would flatter every partial run.

And the provenance line is not decoration. Here is what `recheck run` says
about the calibration paper on a machine with no GPU and a 512 MB download
ceiling:

> **Provenance:** numbers aggregated from artifacts committed at `76c4369`, not
> re-scored; not re-run: `score_surprisal_multimodel.py`
> (`MISSING_DEPENDENCY`, `BUDGET_EXCEEDED`)

Six of its nine cells came back green. All six were read from data the repo
committed, because the script that would regenerate them imports `minicons` —
which `requirements.txt` never pins — and would pull 5.9 GB of checkpoints. A
reader who does not know that cannot read those six greens correctly, so the
report says it above the table rather than in a footnote.

---

## How it decides

A cell is **GREEN** when the rerun lands within tolerance, *or* within the
uncertainty the paper itself reported — if the authors printed `1.42 ± 0.21`,
a rerun at `1.60` is inside their own interval and our default band has no
standing to overrule it.

**YELLOW** is outside tolerance but the same direction and within 3× the band.
**RED** is a sign flip — regardless of magnitude, because for an effect-size
table `-0.05` against a reported `+0.05` contradicts the claim — or anything
beyond the yellow band.

The default band is `max(absolute, |value| × 0.05)`, where `absolute` scales
with magnitude so a surprisal delta near 1 and a perplexity in the hundreds are
both judged sensibly. Override with `--tolerance 0.02` or
`--tolerance rel=0.01,abs=0.5`.

Different tables in one paper rarely deserve the same sensitivity, so bands can
be set per table in a `recheck.toml` — picked up automatically from beside your
paper.json, or passed with `--tolerance-config`:

```toml
[tolerance]
relative = 0.05

[tolerance.tables."tab:npz"]   # keyed by \label, or table-N when unlabelled
relative = 0.005               # unset fields inherit from [tolerance]
```

Copy `recheck.toml.example` to start. `--tolerance` overrides the config's
default band but never its per-table entries.

Full rules, the JSON contracts, and every failure code: [docs/SCHEMA.md](docs/SCHEMA.md).
Why it works this way: [docs/DECISIONS.md](docs/DECISIONS.md).

---

## Extraction

LaTeX only — tables are parsed from the paper's e-print source, not the PDF.
Extraction is fully deterministic; there is no model call in this path, so the
committed golden files are a real regression signal.

Handled: `tabular` / `tabularx` / `tabular*` / `longtable`, booktabs and
`\hline` rules, `\multicolumn` and `\multirow`, multi-level headers separated
by `\cmidrule`, section rows inside the body, math-mode cells, `±` and
parenthesised and subscript uncertainties, scientific notation
(`$2.3\cdot10^{19}$`), `%` and `×` units, significance markers, bold/italic
emphasis (recorded, never interpreted), captions and labels in either order,
unlabelled tables, `\input`/`\include` across a multi-file project, and
user-defined `\newcommand` / `\def` macros — which real papers use for their
column headers.

Citation keys, spacing macros, and rules are stripped with their arguments, so
a row label reads `ByteNet`, not `ByteNet NalBytenet2017`.

Sanity-checked against the *Attention Is All You Need* e-print
(`recheck extract 1706.03762`): 4 tables, 136 numeric cells, every column
header resolved.

Every cell gets a human-readable address:

```
Table 2 › Pythia-1.4B › NP/Z › surprisal delta
```

which is what a results file joins against, and what you read in the report.

---

## Development

```bash
uv run pytest -m "not network"   # 306 tests, no network
uv run pytest -m network         # also hit arXiv for real
uv run ruff check .
python scripts/update_golden.py  # only when an extraction change is intended
python scripts/sweep_papers.py   # extraction across a corpus of real arXiv papers
```

Golden files are committed. Tests never self-heal — regenerating is a
deliberate act, and the resulting diff is the thing you review.

`sweep_papers.py` is the other half of the net, and in practice the more
productive one. Fixtures encode what their author already thought could go
wrong; every extraction bug found so far was invisible to them and obvious the
first time real papers went through. It exits non-zero on a crash or a
structurally broken address, so it can gate a release.

Every failure path in the taxonomy is provoked deliberately in
`tests/test_runner.py` against synthetic repos built on the fly, and each test
asserts the code *and* that its evidence names something concrete — a file, a
package, a model, a measured cost. "There is a reason" is not enough to pass,
because a generic reason is the failure mode this tool exists to prevent.

The calibration fixture is regenerated by the executor itself:

```bash
python scripts/build_calibration_results.py --repo ~/price-of-reanalysis \
    --out tests/fixtures/results/garden_path_results.json
```

## License

MIT — see [LICENSE](LICENSE).

## Roadmap

1. **Table extraction + diff engine** — done.
2. **Execution agent** — sandboxed env setup, script-to-table mapping, budget
   enforcement behind `--max-hours` / `--max-gpu` / `--max-download-mb` — done.
3. **Launch** — two public probing/surprisal papers end to end, a container
   sandbox backend, polished report format, README GIF. See [NEXT.md](NEXT.md).
