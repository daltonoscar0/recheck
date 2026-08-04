# Recheck

Agent SDK-based CLI: `recheck <arxiv-url> --repo <url>`. Takes an ML/NLP
paper plus its repo and produces a cell-by-cell reproduction report: the
paper's tables next to freshly-run numbers, green/yellow/red, with honest,
specific failure reasons (missing checkpoint, gated dataset, undocumented
flag) for anything that can't run.

## Pipeline
1. **Paper side** — extract tables + experiment descriptions from the arXiv
   LaTeX source (pull the .tar source; LaTeX, not PDF).
2. **Repo side** — map scripts/configs to tables by reading code + README.
3. **Execution** — sandboxed env setup (container + uv), budgeted runs
   behind `--max-hours` / `--max-gpu`, seeds respected where specified.
4. **Diff report** — per-cell comparison with tolerance bands, plus a
   failure taxonomy for everything unrunnable. The taxonomy is half the
   product's value: every unrunnable cell gets a specific, evidenced reason,
   never a generic "failed".

## Hard rules
- v1 scope is **tables only** — no figures. Push back if scope creeps.
- **Read-only outside the sandbox.** Target repos are never mutated;
  everything executes in the sandbox; the report is the only artifact.
- Compute budgets are always enforced, no exceptions.
- No AI attribution anywhere in this repo: no Claude/AI mentions in
  commits, code comments, README, or config. Commit as the repo's
  configured git user only, with plain conventional-commit messages.

## Calibration target
First target is the garden-path paper ("Price of Reanalysis") and its repo:
surprisal-based garden-path effects in GPT-2-large / Pythia-1.4B, NP/Z
stimuli. Then two public probing/surprisal papers for launch.

## Engineering conventions
- Python 3.11+, uv for env management, `pyproject.toml`, src/ layout.
- Typer or argparse for the CLI, rich for terminal report rendering.
- pytest with fixtures; every extraction/diff behavior gets a test.
- Type hints throughout, ruff clean.

## Milestones
1. Table extraction + diff engine, validated against manually-run numbers
   from the calibration paper.
2. Env-setup + execution agent with budget enforcement, end-to-end.
3. Two public papers, polished report format, README GIF, launch.
