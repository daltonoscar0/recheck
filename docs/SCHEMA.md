# Data contracts

Two JSON documents move between the halves of the pipeline. Both are public
contracts: anything reading or writing them may rely on the fields below.

`schema_version` is required in both. Recheck reads any file whose **major**
version matches its own and rejects anything else with a clear error rather
than guessing. Current version: **1.0**.

---

## Cell addresses

An address is the join key between the two files, and it is deliberately
human-readable so a results file can be written by hand:

```
Table 2 › Pythia-1.4B › NP/Z › surprisal delta
└──┬───┘ └─────┬─────┘ └──────────┬──────────┘
   │           │                  └── column header path, outermost first
   │           └───────────────────── row header path (section, then stub columns)
   └───────────────────────────────── 1-based table order in the document
```

The separator is `" › "` (U+203A with single spaces). Rules:

- The row path is the section header, if the table has one, followed by each
  stub column's text for that row.
- The column path is every distinct header row entry above that column, from
  the outermost `\multicolumn` group down to the leaf.
- A column with no header text gets `column N` so the address stays unique.
- If two cells still resolve to the same address, later ones get a ` #2`,
  ` #3` suffix in document order.

Addresses are stable under reformatting of the LaTeX but **not** under
renaming a model or reordering tables. Treat them as content-derived keys,
not permanent identifiers.

---

## paper.json

Produced by `recheck extract`.

```jsonc
{
  "schema_version": "1.0",
  "source": {
    "arxiv_id": "2401.12345",      // "" when extracted from a local file
    "main_tex": "main.tex"          // file the document was rooted at
  },
  "tables": [
    {
      "id": "tab:npz",              // \label, or "table-N" when unlabelled
      "index": 1,                   // 1-based order of appearance
      "label": "tab:npz",           // null when the table has no \label
      "caption": "Surprisal difference in bits …",
      "environment": "tabularx",    // the inner tabular environment used
      "column_headers": [           // header path per column, by column index
        ["Model"], ["Condition"], ["Surprisal Δ", "NP/Z"], ["Surprisal Δ", "NP/S"]
      ],
      "cells": [ /* see below */ ],
      "context": {
        "referencing_paragraphs": [ // paragraphs citing this table via \ref
          "Table 1 breaks the effect down by construction and condition. …"
        ]
      }
    }
  ]
}
```

### Cell

```jsonc
{
  "address": "Table 1 › GPT-2-large › Ambiguous › Surprisal Δ › NP/Z",
  "row_path": ["GPT-2-large", "Ambiguous"],
  "col_path": ["Surprisal Δ", "NP/Z"],
  "row": 1,                         // 1-based body row, section rows excluded
  "col": 2,                         // 0-based grid column
  "raw": "$1.42 \\pm 0.21$",        // LaTeX exactly as it appeared
  "text": "1.42 ± 0.21",            // flattened, what a reader sees
  "is_numeric": true,
  "value": 1.42,                    // null when is_numeric is false
  "uncertainty": 0.21,              // null when the paper reported none
  "uncertainty_kind": "pm",         // none | pm | paren | subscript
  "unit": "none",                   // none | percent | times
  "emphasis": ["bold"],             // bold | italic | underline, recorded only
  "markers": ["*"]                  // significance stars, comparators, daggers
}
```

Notes:

- Only **data** cells appear. Stub-column text lives in `row_path`, and section
  rows contribute to `row_path` rather than becoming cells of their own.
- `emphasis` is recorded, never interpreted. Bold usually means "best in
  column", but that is the reader's inference, not the extractor's.
- `is_numeric` is false for `--`, `n/a`, and any cell whose text is not
  entirely a number once units, markers and uncertainty are removed. This is
  what keeps `GPT-2-large` from being read as the number 2.

---

## results.json

Produced by `recheck run`. Still hand-writable, which is the point of readable
addresses.

```jsonc
{
  "schema_version": "1.0",
  "run": {
    "repo": "https://github.com/example/garden-path",
    "commit": "a3f91c2",
    "commit_full": "a3f91c2…",       // 40 chars, when the repo was a git repo
    "sandbox": "LocalSandbox",
    "mapper": "deterministic",
    "environment": { /* see below */ },
    "budget":      { /* see below */ },
    "entry_points": [ /* see below */ ],
    "note": "free text, shown in the report header as Provenance"
  },
  "cells": [
    { "address": "Table 1 › GPT-2-large › Ambiguous",
      "value": 16.879, "uncertainty": 3.074, "n_seeds": 3 },

    { "address": "Table 1 › GPT-2-large › Δ",
      "value": 6.114,
      "caveats": ["derived as (… › Ambiguous) − (… › Control)"] },

    { "address": "Table 1 › GPT-3 davinci › Ambiguous",
      "failure": { "code": "MISSING_CHECKPOINT", "evidence": "openai/davinci is API-gated …" } }
  ]
}
```

Every cell carries **either** a `value` **or** a `failure`. An entry with
neither is reported as `UNRUNNABLE` / `OTHER` with evidence saying so, because
silently dropping it would overstate reproduction.

`caveats` is optional and independent of that choice. It holds things that are
true about a number that was *still produced* — a script that samples
randomness without a seed, a column derived by subtraction. A caveat never
replaces a value; it rides alongside it into the report's reason column, so the
reader sees both. Codes may appear inside a caveat string
(`"NONDETERMINISTIC_NO_SEED: …"`) without the cell being a failure.

### run

```jsonc
"environment": {
  "requirements": "requirements.txt",   // "" when the repo declares none
  "requirements_kind": "requirements.txt | pyproject.toml | environment.yml | none",
  "python": "/…/venv/bin/python",
  "installed": ["numpy==2.0.0", "…"],   // what was actually installed
  "note": "installed from requirements.txt with uv"
},
"budget": {
  "max_hours": 2.0, "max_gpu_hours": 0.0, "max_download_mb": 512.0,
  "spent_hours": 0.0031, "spent_gpu_hours": 0.0, "downloaded_mb": 0.0
},
"entry_points": [
  {
    "table": "tab:calibration",
    "status": "executed | refused | failed | no_candidate",
    "script": "score_surprisal_multimodel.py",
    "estimate": { "wall_hours": 0.01, "gpu_hours": 0.0, "download_mb": 5930.0,
                  "basis": ["… loads gpt2-large (~3.1 GB of weights)"] },
    "blockers": [ { "code": "BUDGET_EXCEEDED", "evidence": "…" } ],
    "caveats": ["NONDETERMINISTIC_NO_SEED: …"],
    "seconds": 12.4
  }
]
```

`blockers` is a list because more than one thing can be true at once — a script
can both import a package the repo never pinned *and* need 6 GB of weights.
The first blocker is the code each affected cell reports; the rest stay on the
record so nothing checkable is thrown away.

`status` distinguishes **refused** (we declined to start, and `blockers` says
why) from **failed** (we started and it exited non-zero). Both produce coded
cells; only the second spent any budget.

### Failure codes

| Code | Meaning |
| --- | --- |
| `MISSING_CHECKPOINT` | Model weights the table depends on are not published |
| `GATED_DATASET` | Data requires credentials, a licence, or manual approval |
| `MISSING_DEPENDENCY` | A required package or binary could not be installed |
| `UNDOCUMENTED_FLAG` | The script needs an argument the paper never specifies |
| `NONDETERMINISTIC_NO_SEED` | Run is stochastic and no seed is documented |
| `BUDGET_EXCEEDED` | Run exceeded the configured compute budget |
| `ENV_UNRESOLVABLE` | No dependency set satisfies the stated requirements |
| `SCRIPT_NOT_FOUND` | No script in the repo produces this table |
| `OTHER` | Outside the taxonomy — **evidence is mandatory** |

`evidence` should name something checkable: a file and line, a package
specifier, a resolver error, a measured cost against the budget. Every code
above is reachable from `recheck run`, and each has a test asserting both the
code and that its evidence names something concrete.

`NONDETERMINISTIC_NO_SEED` is the one code that normally appears as a
**caveat** rather than a failure: the run happens, the number is reported, and
the caveat travels with it. Reporting nothing there would hide a value the
reader can still use.

---

## The mapping plan

`recheck run` writes the script-to-table mapping to
`~/.cache/recheck/plans/<commit>.<mapper>.json` (override with `--plan-cache`).
It is regenerated when the commit changes, or on `--refresh-plan`.

```jsonc
{
  "plan_version": "1",
  "commit": "76c4369",
  "mapper": "deterministic",
  "tables": [{
    "table_id": "tab:calibration",
    "table_index": 1,
    "cells": [
      { "address": "Table 1 › GPT-2-large › Ambiguous",
        "recipe": { "kind": "aggregate",
                    "artifact": "phase1_stimuli/surprisal_gpt2-large.csv",
                    "value_column": "critical_region_surprisal",
                    "statistic": "mean",
                    "filters": [{"column": "condition", "value": "ambiguous"},
                                {"column": "construction", "value": "NPZ"}],
                    "uncertainty": "stdev" } },
      { "address": "Table 1 › GPT-2-large › Δ",
        "recipe": { "kind": "difference", "minuend": "…", "subtrahend": "…" } },
      { "address": "Table 1 › GPT-3 davinci › Ambiguous",
        "failure": { "code": "MISSING_CHECKPOINT", "evidence": "…" } }
    ],
    "candidates": [{ "script": "score_surprisal_multimodel.py",
                     "argv": ["python", "score_surprisal_multimodel.py"],
                     "score": 6.5, "why": "writes 'phase1_stimuli/surprisal_'; …" }],
    "considered": ["build_stimuli.py", "…"]
  }]
}
```

This file is the artifact to read when a number looks wrong. "You took the mean
of the wrong column" is a fixable bug report; "the number is wrong" is not.

---

## Diff statuses

| Status | Meaning |
| --- | --- |
| `GREEN` | Within tolerance, or within the paper's own reported ±1 std |
| `YELLOW` | Outside tolerance, same sign, within `yellow_multiplier` × band |
| `RED` | Sign differs from the paper, or beyond the yellow band |
| `UNRUNNABLE` | Attempted and could not run; carries a failure code |
| `NOT_ATTEMPTED` | The results file has no entry for this cell at all |

`NOT_ATTEMPTED` is deliberately distinct from `UNRUNNABLE`: "we never tried"
and "we tried and it is impossible" are different claims about a paper, and
collapsing them would flatter the report.

### Tolerance

For a paper value `p`, the band is `max(absolute, |p| × relative)`, where
`relative` defaults to `0.05` and `absolute` defaults by magnitude:

| \|p\| | default absolute |
| --- | --- |
| < 1 | 0.01 |
| < 10 | 0.05 |
| < 100 | 0.5 |
| ≥ 100 | 1.0 |

`--tolerance` takes a bare number (relative) or `rel=…,abs=…,yellow=…`.

Per-table bands come from a TOML config — `--tolerance-config path.toml`, or
the nearest `recheck.toml` found by walking up from the paper.json:

```toml
[tolerance]
relative = 0.05
absolute = 0.1
yellow_multiplier = 3.0

[tolerance.tables."tab:npz"]     # keyed by the table's id: \label, or table-N
relative = 0.005                 # unset fields inherit from [tolerance]
```

Precedence, strongest first: `--tolerance` on the command line, then
`[tolerance]` in the config, then the built-in defaults. Per-table entries
always apply to their own table — an explicit per-table band is more specific
than a global flag, so `--tolerance` does not erase it.

See `recheck.toml.example` for a fully commented file.

When the paper reports an uncertainty, a result within ±1 of it is `GREEN`
regardless of the configured band — the paper's own error bar outranks our
default.
