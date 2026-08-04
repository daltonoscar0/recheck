# Decisions

Judgment calls made while building recheck, with the reasoning that produced
them. Reverse any of these freely — they are recorded so the reversal is
deliberate.

## Extraction

**Hand-rolled LaTeX tokenizer instead of pylatexenc.** Table markup in ML
papers is a small, ugly dialect, and the failure modes that matter are nested
braces, `$…$`, `\multicolumn`, and macro soup. A general parser adds a
dependency and still needs table-specific logic on top. The tokenizer is ~250
lines and every fixture passes.

**No LLM call anywhere in extraction.** Determinism is what makes the golden
files a real regression signal. A model in this path would make every diff
advisory.

**Only data cells are emitted; stub columns become `row_path`.** A cell needs
an address, and the address is built from the labels around it. Emitting the
labels as cells too would double-count and make "how many cells reproduced"
meaningless.

**Stub width is detected, not fixed at 1.** The leading columns whose body
entries are all non-numeric are treated as labels. This handles the common
`Model | Condition | …` layout without configuration. Guarded to at least 1 and
never all columns, so a table of pure numbers still gets row labels.

**Header rows are everything before the first `\midrule` or `\hline`.**
`\cmidrule` explicitly does not end the header, since it is what separates
levels *within* a multi-level header. Tables with no rules at all fall back to
treating the first row as the header.

**A cell is numeric only on a full match.** After stripping units, markers and
uncertainty, what remains must be entirely a number. Partial matching would
read `GPT-2-large` as `2` and silently corrupt every comparison downstream —
the worst available failure mode, because it produces confident wrong output.

**Emphasis is recorded, not interpreted.** Bold usually means "best in column",
but that is an inference about the paper's argument. Milestone 2 can use it;
extraction just reports it.

**Multirow continuation reads from empty placeholder cells.** Real LaTeX
continuation rows carry `&` placeholders, so an empty cell under an active
carry inherits the carried value. This is simpler than tracking occupancy and
matches how the markup is actually written.

**User-defined macros are expanded before parsing.** Validating against the
real *Attention Is All You Need* source showed its column headers were
`\dmodel` and `\dff` — `\newcommand` shorthands defined in the preamble.
Without expansion those columns had no header at all. `\newcommand` and simple
`\def` are collected and substituted, bounded to a few passes so a
self-referential macro degrades instead of hanging. Structural commands
(`\multicolumn`, `\begin`, …) are protected from being shadowed.

**Citation keys and spacing macros are dropped with their arguments.** The same
validation produced row labels like `ByteNet NalBytenet2017` and
`0pt 2.0ex base`. Neither is anything a reader sees, and both corrupt the
address a results file has to join against.

**Command names end at `(?![a-zA-Z])`, not `\b`.** `_` is a word character, so
`\b` never matched `\epsilon_{ls}` and the symbol was silently dropped as an
unknown command, leaving a header reading `_ls`. This class of bug is invisible
without real-paper fixtures.

**Scientific notation is parsed.** `$2.3\cdot10^{19}$` is a number, and reading
it as the bare mantissa `2.3` would be off by nineteen orders of magnitude in
exactly the cost and parameter-count columns people care about.

**Unlabelled tables get `table-N`.** Papers cite by number, and a synthetic id
keeps the JSON keyable. Real `\label`s are preferred when present.

**`\ref{tab:x}` is resolved to the table number in context paragraphs.**
Storing "Table tab:npz shows…" would be useless to a human and to the
milestone-2 mapper. Refs to unknown labels keep their label text.

## Diff

**`NOT_ATTEMPTED` is a separate status from `UNRUNNABLE`.** The brief listed
four statuses plus a taxonomy, but "the results file has no entry for this
cell" and "we tried and it cannot run" are different claims about a paper.
Collapsing them into `UNRUNNABLE` would let a partial run look like a thorough
one, which is exactly the dishonesty this tool exists to prevent.

**A result inside the paper's own reported uncertainty is `GREEN`.** If a paper
says `1.42 ± 0.21`, a rerun at `1.60` is inside the interval the authors
themselves published. Our default band has no standing to overrule that.

**Sign flips are `RED` regardless of magnitude.** For an effect-size table, a
result of `-0.05` against a reported `+0.05` is small in absolute terms and
contradicts the paper's claim. Direction outranks distance.

**Absolute tolerance defaults by magnitude.** A fixed absolute band is either
meaningless for perplexities in the hundreds or brutal for surprisal deltas
near 1. The magnitude ladder is coarse on purpose; `--tolerance` overrides it.

**Units are reported, never auto-converted.** When a paper marks a cell `%` or
`×`, the comparison notes it and compares the numbers as given. Guessing that a
result of `0.712` means the paper's `71.2%` would be a silent unit conversion,
and silent conversions are how reproduction reports become wrong.

**`OTHER` is the only code requiring evidence.** The named codes are
self-describing; `OTHER` is an escape hatch, and an escape hatch without
evidence is the generic "failed" this taxonomy exists to prevent.

**Exit codes: 0 clean, 1 any RED, 2 bad input, 3 not implemented.** Makes the
CLI usable in CI without parsing output. `run` exits 3 because reporting
success for a pipeline that has not executed anything would be a lie.

## Project

**`recheck run` extracts and then stops.** Rather than stubbing the whole
command, it does the half that works and states plainly which half does not.
The full argument surface is already final, so milestone 2 changes behaviour
without changing the interface.

**Golden files are committed and regenerated by an explicit script.** Running
`scripts/update_golden.py` is a deliberate act; the resulting diff is the
review artifact. Tests never self-heal.

**The calibration results are aggregated from committed CSVs, not re-scored.**
`scripts/build_calibration_results.py` reads the garden-path repo read-only and
recomputes the table's numbers from per-item surprisal data. That validates the
diff engine against real numbers today, and it is honest about not having
re-run the models — `run.note` says so in the file itself.

**The paper side of calibration is a transcribed fixture, not real LaTeX.**
The garden-path paper has no LaTeX source available locally, only a PDF, and
extraction operates on LaTeX by design. `garden_path_calibration.tex` carries
the paper's real numbers in representative markup. When the source is
exported, point `recheck extract` at it and the fixture can retire.

## Acquisition

**A dirty local tree is refused, not silently pinned to HEAD.** The report's
whole claim is "this is what the code at this commit produces". Pinning a
commit whose working tree differs would make that claim false in a way nobody
could detect from the report. `--allow-dirty` exists, and a run using it says
so.

**Copies are made with `git archive HEAD`, not `cp -r`.** It reads the object
store, emits only tracked files, and cannot write to the source. The
calibration repo carries a 1.9 GB `.venv` beside its code; a recursive copy
would have pulled it into every sandbox. A non-git directory falls back to a
filtered `copytree`, and only under `--allow-dirty`, because it cannot be
pinned.

**Both a short and a full commit hash are recorded.** The short one is what a
human reads in a report header; the full one is what survives a repo growing
enough for seven characters to collide.

## Mapping

**Cells are routed by aligning names, not by asking a model.** A cell address
is a list of names the paper chose (`GPT-2-large`, `Ambiguous`); a repo spells
the same names in filenames (`surprisal_gpt2-large.csv`) and in categorical
column values (`condition = ambiguous`). When every name in an address is
accounted for, the cell gets a recipe. This is checkable, testable, and
identical between runs — and routing is the layer everything downstream trusts,
so a stage that silently changed its mind between runs would make every diff
advisory. `Mapper` is a protocol; a model-backed mapper is additive.

**Not adding the model call yet is the whole reason milestone 2 has a
regression signal.** NEXT.md flagged mapping as the one stage where a model
call is appropriate, and it still is — for repos whose structure defeats name
alignment. The deterministic mapper routes the calibration paper completely, so
adding a model now would buy nothing and cost determinism. The seam is the
protocol; the fallback is what shipped.

**Punctuation between two digits survives normalisation.** `GPT-2-large` and
`gpt2-large` must match, so punctuation is generally dropped — but that also
collapsed `pythia-1.4b` into `pythia-14b`, which are different models. Digit
boundaries are kept as `.`; everything else is noise. A normal form that
conflated parameter counts would route a cell to the wrong checkpoint and then
report the number as a reproduction, which is the worst available failure.

**Categorical values match exactly; filenames match by containment.** A
`condition` column holding `ambiguous` and `ambiguous_control` must not have
both rows claimed by the paper's `Ambiguous`. Filenames carry extra words by
nature (`phase1_stimuli/surprisal_gpt2-large.csv`), so containment is right
there and exactness is right for values.

**Reading a file is not producing it.** Every repo reads its own outputs
somewhere; the calibration repo has five analysis scripts that `pd.read_csv`
the surprisal tables and one that writes them. Ranking by "mentions the path"
put all six at the same score. A path literal counts only when a write call
appears within three lines and no read call appears on the same line.

**A path prefix must not run into a longer name.** Scripts build outputs with
f-strings, so `produces` matches prefixes — but `phase1_stimuli/surprisal_gpt2-large`
is a prefix of `…_initial.csv` too. A prefix counts only when the next source
character cannot continue the name, and only when it reaches past the
directory, so a bare `phase1_stimuli/` matches nothing.

**A model the repo's own enumeration omits is `MISSING_CHECKPOINT`, not
`SCRIPT_NOT_FOUND`.** When a repo writes `models = [...]` and the paper's other
rows are all in that list while one is not, the repo is telling you precisely
which checkpoint it cannot reach. Requiring the sibling rows to match is what
keeps this from firing on every unmatched name.

**A difference column is derived only when the row is unambiguous.** Papers
print effect-size columns no script writes. Deriving `Δ` from exactly two other
numeric cells in the same row is safe *because the derivation is written into
the plan and repeated as a caveat on the cell*. Three sibling columns would
mean guessing which pair the paper meant, and that is how a report starts
inventing numbers — so that case fails instead.

**A tie between value columns is a failure, not a coin flip.** If two numeric
columns match the caption equally well, the report says so and names both.

## Execution

**Static checks, then the budget gate, then the environment, then the run.**
Static dependency checks are free and produce the most specific code available,
so they go first. The budget gate goes next because NEXT.md is right that an
estimate breaching a ceiling must not spend a second proving it — and building
an environment is itself a cost. This ordering is why the calibration repo
reports `MISSING_DEPENDENCY` (minicons is imported and never pinned) *and*
`BUDGET_EXCEEDED` (5.9 GB of weights against a 512 MB ceiling): both are true,
both are recorded, the first is what the cells carry.

**A third ceiling, `--max-download-mb`.** Papers fail against three scarcities,
not two. On a machine with no GPU, `--max-gpu 0` never fires for a CPU-pinned
scoring script — but six gigabytes of checkpoints is still a refusal worth
making, and "gpt2-large is about 3.1 GB and the ceiling is 512 MB" is an
argument the user can answer by raising the ceiling.

**Cost estimates err high and always show their basis.** An underestimate
spends the user's budget on a run that was never going to fit. An overestimate
produces a refusal naming the model and the size, which is checkable and
overridable. Sizes come from a small table of published checkpoints plus a
parameter-count regex on the id.

**Mid-run enforcement kills, it does not ask.** The sandbox polls the deadline
and terminates the process group at it. Output goes to files rather than pipes:
a run that filled a 64 KB pipe buffer while we polled would block forever, and
a budget that deadlocks is not a budget.

**`LocalSandbox` is containment, not a security boundary.** It scrubs the
environment so a target repo's code never sees the operator's API keys,
redirects `HOME` and every cache inside the workdir, and sets a CPU rlimit.
That is honest process isolation and it is what shipped. `Sandbox` is a
protocol; a container backend plugs in there, and is the first thing to add
before running code from repos nobody has read.

**Blockers are a list, and the first one is what the cells report.** More than
one thing can be true about a script. Collapsing them would throw away
checkable facts; reporting all of them per cell would bury the reason.

**Committed artifacts are used when a run is refused, and the report says so
loudly.** The alternative was an all-red calibration report: the garden-path
repo's scoring script needs 6 GB of weights, so under this session's ceilings
nothing re-runs. But comparing a paper's printed numbers against what its own
committed data actually says is a real check that real papers fail — so those
cells get values, `run.note` states they were aggregated and not re-scored and
names the blocker, and the report prints that as a `Provenance:` line above the
tables. `--no-committed-artifacts` turns it off and makes those cells carry the
blocker instead.

**Provenance lives in `run`, not on each cell.** Per-cell provenance would be
better, and it is the obvious next schema change — but `schema_version` is part
of the extraction goldens, and bumping it to say "this number came from a
committed file" would have moved five golden files in a milestone whose brief
was not to perturb extraction at all. `run.entry_points` carries the same fact
per table, which is the granularity a run actually has.

**`caveats` was added to `ResultCell`; `schema_version` was not bumped.**
NEXT.md asks that an unseeded stochastic script be run anyway, its value
reported, and the caveat carried by the status — which the value-xor-failure
rule had no room for. `caveats` is an optional, additive field: absent from
every file written before it existed, ignored by readers that do not know it,
and rendered into the report's reason column by the ones that do. Within a
major version that is a compatible addition, and the alternative was either
dropping a usable number or moving the goldens.

**A repo that commits none of its outputs is mapped after running, not before.**
The mapper needs a file's columns to route a cell to it, so a repo whose CSVs
only exist post-run has nothing to align against. Entry points therefore get a
weak, capped signal from their source text — enough to clear the confidence
floor when a script genuinely mentions the table's terms, never enough to
outrank actually writing the file — and after a successful run the table is
re-mapped against what now exists.

**`recheck run` no longer exits 3.** Exit 3 meant "the requested stage is not
implemented", which was true of the execution half and is not any more. It
stays documented as reserved rather than being recycled, since scripts written
against it should keep failing loudly rather than start passing quietly.
`--repo` is now required and its absence is exit 2, because reproducing a paper
without its code is not a thing recheck can do.
