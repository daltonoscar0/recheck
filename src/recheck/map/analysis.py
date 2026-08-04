"""Static reading of a repo's scripts.

Everything here answers a question about a script without running it: which
checkpoints it can score, which file it writes, what it imports, whether it
samples randomness. The executor uses these to decide what a run would cost and
what to say when it refuses; the mapper uses them to route cells.

These are heuristics over source text, and they are written to fail towards
"say something specific and checkable" rather than towards "assume it is fine".
"""

from __future__ import annotations

import re

from .inventory import ScriptFile
from .tokens import contained

#: Shortest literal path fragment we will accept as "this script writes that file".
MIN_PRODUCER_FRAGMENT = 8

#: `models = ["gpt2-large", ...]`, `MODEL_NAMES = (...)` — how repos say which
#: checkpoints they know how to score.
_MODEL_LIST = re.compile(
    r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*[\[\(]([^\]\)]*)[\]\)]",
    re.MULTILINE,
)
_STRING_LITERAL = re.compile(r"""['"]([^'"\n]{2,})['"]""")
_IMPORT = re.compile(r"^[ \t]*(?:from[ \t]+([A-Za-z_][\w.]*)|import[ \t]+([A-Za-z_][\w., ]*))",
                     re.MULTILINE)

#: Hugging Face ids look like `org/name` or a bare well-known family name.
_HF_ID = re.compile(r"""['"]([A-Za-z0-9][\w.\-]*(?:/[\w.\-]+)?)['"]""")

_RANDOMNESS_MARKERS = (
    "random.", "np.random", "numpy.random", "torch.rand", "randperm",
    "do_sample=True", "shuffle=True", "bootstrap",
)
_SEED_MARKERS = ("seed(", "seed =", "seed=", "set_seed", "manual_seed", "random_state")


def model_enumerations(scripts: list[ScriptFile]) -> list[tuple[str, list[str]]]:
    """`(script path, [model ids])` for every model list literal in the repo."""
    found: list[tuple[str, list[str]]] = []
    for script in scripts:
        for match in _MODEL_LIST.finditer(script.text):
            variable = match.group(1).lower()
            if "model" not in variable and "checkpoint" not in variable:
                continue
            ids = _STRING_LITERAL.findall(match.group(2))
            if ids:
                found.append((script.path, ids))
    return found


def names_a_model(name: str, model_ids: list[str]) -> bool:
    """Whether a paper's label refers to one of these checkpoint ids."""
    return any(contained(name, model_id) or contained(model_id, name) for model_id in model_ids)


def referenced_model_ids(script: ScriptFile) -> list[str]:
    """Every string in the script that could be a Hugging Face model id.

    Deliberately loose. The cost estimator only needs to know that something
    large is about to be downloaded; a false positive is a refusal the report
    explains, and a false negative is an unbudgeted download.
    """
    seen: list[str] = []
    for match in _HF_ID.finditer(script.text):
        candidate = match.group(1)
        if _looks_like_model_id(candidate) and candidate not in seen:
            seen.append(candidate)
    return seen


_DATA_SUFFIXES = (".csv", ".json", ".txt", ".png", ".tsv", ".xlsx", ".md")
_BARE_MODEL_NAME = re.compile(r"(gpt2|gpt-2)(-(small|medium|large|xl))?", re.IGNORECASE)


def _looks_like_model_id(candidate: str) -> bool:
    if candidate.endswith(_DATA_SUFFIXES):
        return False
    return "/" in candidate or _BARE_MODEL_NAME.fullmatch(candidate) is not None


#: How far past a path literal to look for the call that consumes it. Scripts
#: routinely name the file on one line and write it two lines later.
_WRITE_WINDOW_LINES = 3

_READ_MARKERS = ("read_csv", "read_table", "read_excel", "loadtxt", "np.load", "json.load",
                 "pd.read", "genfromtxt")
_WRITE_MARKERS = ("to_csv", "to_json", "savetxt", "savefig", "json.dump", "np.save",
                  "writerow", "writeheader", ".write(")


def _is_write_site(text: str, index: int) -> bool:
    """Whether the path literal at `index` is being written rather than read.

    Every repo reads its own outputs somewhere. Counting a `pd.read_csv` as
    "this script produces that file" would rank six analysis scripts above the
    one scoring script that actually writes it.
    """
    line_start = text.rfind("\n", 0, index) + 1
    line_end = text.find("\n", index)
    line = text[line_start : line_end if line_end >= 0 else len(text)]
    if any(marker in line for marker in _READ_MARKERS):
        return False

    window = line
    cursor = line_end
    for _ in range(_WRITE_WINDOW_LINES):
        if cursor < 0:
            break
        following_end = text.find("\n", cursor + 1)
        window += "\n" + text[cursor + 1 : following_end if following_end >= 0 else len(text)]
        cursor = following_end
    if any(marker in window for marker in _WRITE_MARKERS):
        return True
    return "open(" in window and any(mode in window for mode in ('"w', "'w", '"a', "'a"))


def produces(artifact_path: str, script: ScriptFile) -> str | None:
    """The longest literal fragment of `artifact_path` this script writes.

    Scripts build output paths with f-strings, so exact matches are rare. A
    prefix counts only when the next character in the source is not more of the
    same name — otherwise `phase1_stimuli/surprisal_gpt2-large` would match the
    script that writes `surprisal_gpt2-large_initial.csv` — and only when it
    reaches past the directory, so a bare `phase1_stimuli/` matches nothing.
    """
    directory_length = artifact_path.rfind("/") + 1
    floor = max(MIN_PRODUCER_FRAGMENT, directory_length + 4)
    probe = artifact_path
    while len(probe) >= floor:
        start = 0
        while True:
            index = script.text.find(probe, start)
            if index < 0:
                break
            after = script.text[index + len(probe) : index + len(probe) + 1]
            if not (after.isalnum() or after in "_-") and _is_write_site(script.text, index):
                return probe
            start = index + 1
        probe = probe[:-1]
    return None


def imported_modules(script: ScriptFile) -> dict[str, int]:
    """Top-level module names the script imports, mapped to a 1-based line number."""
    found: dict[str, int] = {}
    for match in _IMPORT.finditer(script.text):
        line = script.text.count("\n", 0, match.start()) + 1
        names = []
        if match.group(1):
            names = [match.group(1)]
        elif match.group(2):
            names = [part.strip() for part in match.group(2).split(",")]
        for name in names:
            root = name.split(".")[0].split(" ")[0].strip()
            if root and root not in found:
                found[root] = line
    return found


def import_line(script: ScriptFile, line_number: int) -> str:
    lines = script.text.splitlines()
    if 1 <= line_number <= len(lines):
        return lines[line_number - 1].strip()
    return ""


def is_stochastic_without_seed(script: ScriptFile) -> bool:
    """Whether the script samples randomness and never fixes a seed.

    Conservative on purpose: a false positive costs a caveat in the report,
    which is far cheaper than silently reporting a number that moves.
    """
    if not any(marker in script.text for marker in _RANDOMNESS_MARKERS):
        return False
    return not any(marker in script.text for marker in _SEED_MARKERS)


def accepts_seed_flag(script: ScriptFile) -> bool:
    return "--seed" in script.text


def forces_cpu(script: ScriptFile) -> bool:
    """Whether the script pins itself to CPU, so no GPU budget is at stake."""
    text = script.text
    if 'device="cpu"' in text or "device='cpu'" in text:
        return "cuda" not in text
    return False
