"""Aliases a repo declares between its own identifiers and the paper's names.

A paper's Table 4 says `LSTM`, `ON-LSTM`, `n-gram`. The CSV behind it says
`vanilla`, `ordered-neurons`, `ngram`. No amount of string normalisation bridges
`vanilla` and `LSTM` — and it should not, because guessing that two unrelated
strings mean the same model is exactly how a reproduction report becomes
confidently wrong.

But repos routinely state the mapping outright, because they need it to render
their own figures:

    RENDER_NAMES = {
        "vanilla": "LSTM",
        "ordered-neurons": "ON-LSTM",
        "rnng": "RNNG",
    }

Harvesting those literals is deterministic, evidenced, and citable — the plan
can say which file and line taught it the alias. An alias is only kept when one
side is a value the data actually contains, which keeps ordinary configuration
dictionaries out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .inventory import RepoInventory, ScriptFile
from .tokens import normalize

# `"key": "value"` inside a dict literal. Deliberately line-scoped: a mapping
# worth harvesting is written one pair per line in every repo we have seen, and
# matching across lines would sweep up unrelated adjacent strings.
#: Lines within this window count as the same dict literal for the purpose of
#: deciding whether a pair has company.
_GROUP_LINES = 8

_PAIR = re.compile(r"""["']([^"'\n]{1,60})["']\s*:\s*["']([^"'\n]{1,60})["']""")

# A display name is short and human-facing; these are never one.
_NOT_A_NAME = re.compile(r"[/\\]|\.(?:csv|tsv|json|png|pdf|txt|py)$|^\s*$|^https?:|%|\{")


@dataclass(frozen=True)
class Alias:
    """One declared equivalence, with the source that declared it."""

    left: str
    right: str
    path: str
    line: int

    @property
    def evidence(self) -> str:
        return f"{self.path}:{self.line} declares {self.left!r} → {self.right!r}"


def _plausible(text: str) -> bool:
    """Could this string be a name a paper or a dataset would use?

    The length floor matters more than it looks: plotting code is full of
    `{"RNNG": "X", "LSTM": "*"}` marker and colour tables, and a one-character
    right-hand side is a glyph, not a model.
    """
    text = text.strip()
    return (
        len(text) >= 2
        and any(character.isalnum() for character in text)
        and not _NOT_A_NAME.search(text)
    )


def harvest(scripts: list[ScriptFile], known_values: set[str]) -> list[Alias]:
    """Collect declared aliases where one side is a value the data contains.

    `known_values` is every categorical value across the repo's artifacts. The
    constraint is what separates a rendering table from a settings dict: an
    alias is only interesting if it connects the data's vocabulary to something
    else.
    """
    normalized_known = {normalize(value) for value in known_values if value}
    found: dict[tuple[str, str], Alias] = {}

    for script in scripts:
        for number, line in enumerate(script.text.splitlines(), start=1):
            for match in _PAIR.finditer(line):
                left, right = match.group(1).strip(), match.group(2).strip()
                if left == right or not (_plausible(left) and _plausible(right)):
                    continue
                left_known = normalize(left) in normalized_known
                right_known = normalize(right) in normalized_known
                # Exactly one side known: the other is the paper-facing name.
                # Both known means the data already speaks both, and neither
                # known means this dict has nothing to do with the results.
                if left_known == right_known:
                    continue
                key = (normalize(left), normalize(right))
                found.setdefault(key, Alias(left=left, right=right, path=script.path, line=number))

    # A rendering table has several entries; a lone `"a": "b"` that happens to
    # mention a data value is far more likely to be a path, a colour, a title or
    # a one-off setting. Requiring company is what separates the two, and a bad
    # alias silently bridges two names that mean different things.
    grouped: dict[tuple[str, int], list[Alias]] = {}
    for alias in found.values():
        grouped.setdefault((alias.path, alias.line // _GROUP_LINES), []).append(alias)
    neighbours: dict[tuple[str, int], int] = {}
    for (path, block), aliases in grouped.items():
        for offset in (-1, 0, 1):
            neighbours[(path, block + offset)] = neighbours.get((path, block + offset), 0) + len(
                aliases
            )

    kept = [
        alias
        for alias in found.values()
        if neighbours.get((alias.path, alias.line // _GROUP_LINES), 0) >= 2
    ]
    return sorted(kept, key=lambda a: (a.path, a.line))


class AliasTable:
    """Bidirectional lookup from a name to the other names a repo equates it to."""

    def __init__(self, aliases: list[Alias] | None = None) -> None:
        self._by_name: dict[str, list[Alias]] = {}
        for alias in aliases or []:
            self._by_name.setdefault(normalize(alias.left), []).append(alias)
            self._by_name.setdefault(normalize(alias.right), []).append(alias)

    def __bool__(self) -> bool:
        return bool(self._by_name)

    def __len__(self) -> int:
        return len(self._by_name)

    def for_name(self, name: str) -> list[tuple[str, Alias]]:
        """Every other-side name this repo equates to `name`, with its evidence."""
        key = normalize(name)
        out: list[tuple[str, Alias]] = []
        for alias in self._by_name.get(key, []):
            other = alias.right if normalize(alias.left) == key else alias.left
            if normalize(other) != key:
                out.append((other, alias))
        return out


def build(inventory: RepoInventory) -> AliasTable:
    """Harvest the alias table for a repo from its scripts and its data."""
    known: set[str] = set()
    for artifact in inventory.artifacts:
        for values in artifact.categories.values():
            known.update(values)
    return AliasTable(harvest(inventory.scripts, known))
