"""One normal form for names that appear in both a paper and a repo.

A paper's prose says `GPT-2-large`, `NP/Z`, `Pythia-1.4B`. The repo says
`surprisal_gpt2-large.csv`, `NPZ`, `EleutherAI/pythia-1.4b`. Joining the two
needs a normal form aggressive enough to survive punctuation and case without
collapsing names that are genuinely different — `pythia-1.4b` and
`pythia-14b` must not become the same string, which is why digits are kept.
"""

from __future__ import annotations

import re

#: Words that carry no identifying information in a caption or a filename.
STOPWORDS = frozenset(
    {
        "a", "an", "the", "of", "for", "in", "on", "at", "to", "and", "or", "by",
        "with", "from", "over", "per", "is", "are", "was", "were", "be", "as",
        "we", "our", "this", "that", "these", "those", "each", "its", "it",
        "table", "tab", "figure", "fig", "results", "result", "values", "value",
        "shows", "show", "reports", "report", "reported", "using", "used", "use",
        "all", "both", "not", "no", "than", "then", "when", "where", "which",
        "csv", "tsv", "json", "py", "data", "out", "output",
    }
)

#: Column headers that mean "the difference between the other columns".
DIFFERENCE_MARKERS = frozenset({"δ", "delta", "diff", "difference", "effect", "gap", "change"})

#: Shortest normalised token we will match by substring. Below this, containment
#: is noise: "np" is inside "input", "np/z" is not.
MIN_CONTAINMENT_LENGTH = 3

_SPLIT = re.compile(r"[^0-9A-Za-z]+")


def normalize(text: str) -> str:
    """Collapse a name to lowercase alphanumerics: `GPT-2-large` -> `gpt2large`.

    Punctuation *between two digits* survives as a `.`, because that is the one
    place it carries meaning: `pythia-1.4b` and `pythia-14b` are different
    models, and a normal form that conflated them would route a cell to the
    wrong checkpoint and report the number as a reproduction.
    """
    out: list[str] = []
    for index, char in enumerate(text):
        if char.isalnum():
            out.append(char.lower())
        elif (
            out
            and out[-1].isdigit()
            and index + 1 < len(text)
            and text[index + 1].isdigit()
        ):
            out.append(".")
    return "".join(out)


def tokenize(text: str) -> list[str]:
    """Split on punctuation into lowercase word tokens, in order."""
    return [part.lower() for part in _SPLIT.split(text) if part]


def content_tokens(*texts: str) -> set[str]:
    """Informative word tokens across several strings, stopwords removed."""
    out: set[str] = set()
    for text in texts:
        for token in tokenize(text):
            if len(token) > 1 and token not in STOPWORDS:
                out.add(token)
    return out


def normalized_phrases(text: str, max_window: int = 3) -> set[str]:
    """Normalised forms of every short run of adjacent words.

    A caption saying "for NP/Z items" tokenises to `np`, `z`, `items`, but the
    repo spells the same category `NPZ`. Joining adjacent tokens recovers it
    without needing a punctuation-aware tokenizer.
    """
    words = tokenize(text)
    phrases: set[str] = set()
    for start in range(len(words)):
        for width in range(1, max_window + 1):
            window = words[start : start + width]
            if len(window) < width:
                break
            phrases.add("".join(window))
    return phrases


def is_difference_marker(text: str) -> bool:
    return normalize(text) in DIFFERENCE_MARKERS


def contained(needle: str, haystack: str) -> bool:
    """True when a normalised name appears inside another normalised string.

    Used for filenames, where `surprisal_gpt2-large.csv` should match the row
    label `GPT-2-large`. Guarded by a minimum length so two-character labels do
    not match half the repo.
    """
    needle_norm = normalize(needle)
    if len(needle_norm) < MIN_CONTAINMENT_LENGTH:
        return False
    return needle_norm in normalize(haystack)


def same_name(left: str, right: str) -> bool:
    """Exact match after normalisation, used for categorical column values.

    Values are matched exactly rather than by containment: a `condition` column
    holding `ambiguous` and `ambiguous_control` must not have both rows claimed
    by the paper's `Ambiguous`.
    """
    return normalize(left) == normalize(right) != ""
