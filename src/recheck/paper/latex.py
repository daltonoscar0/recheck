"""Brace- and math-aware LaTeX primitives.

Deliberately hand-rolled and dependency-free. Table markup in ML papers is a
small, ugly dialect: what matters is surviving nested braces, `$...$`, escaped
ampersands, and `\\` inside cells. A general LaTeX parser buys correctness we
do not need and fails on the macro soup we do encounter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Commands whose sole argument is the visible content, stripped during flattening.
_TRANSPARENT = {
    "textbf", "textit", "texttt", "textrm", "textsf", "textsc", "emph",
    "mathbf", "mathrm", "mathit", "mathsf", "boldmath", "bm",
    "small", "footnotesize", "scriptsize", "large", "Large", "normalsize",
    "centering", "raggedright", "raggedleft", "color", "textcolor",
}

# Commands that carry emphasis we record (formatting often encodes "best result").
_EMPHASIS = {
    "textbf": "bold", "bf": "bold", "mathbf": "bold", "bfseries": "bold", "bm": "bold",
    "textit": "italic", "it": "italic", "emph": "italic", "mathit": "italic",
    "underline": "underline",
}

# Rule/spacing commands that carry no data.
_NOISE = re.compile(
    r"\\(?:toprule|midrule|bottomrule|hline|endhead|endfoot|"
    r"cmidrule|cline|addlinespace|rule|noalign|vspace|hspace|centering|"
    r"resizebox|scalebox|label|caption)\b"
)

# Commands dropped together with their argument: the argument is machinery, not
# content, so leaving it behind would put "tab:main" inside a caption.
_DROP_WITH_ARG = (("label", 1), ("vspace", 1), ("hspace", 1))

# Commands whose *last* argument is the content and whose leading arguments are
# layout: `\resizebox{\textwidth}{!}{x}` must reduce to "x", not "! x".
_UNWRAP_LAST = (("resizebox", 2), ("scalebox", 1), ("makebox", 1), ("parbox", 1))

# `\cmidrule(lr){3-4}` carries a parenthesised trim argument that brace-based
# argument reading would walk straight past, leaving "3-4" in the cell text.
_PARTIAL_RULE = re.compile(r"\\c(?:midrule|line)\b(?:\([^)]*\))?(?:\{[^}]*\})?")

_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "Delta": "Δ",
    "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ",
    "kappa": "κ", "lambda": "λ", "Lambda": "Λ", "mu": "μ", "nu": "ν",
    "pi": "π", "rho": "ρ", "sigma": "σ", "Sigma": "Σ", "tau": "τ",
    "phi": "φ", "Phi": "Φ", "chi": "χ", "psi": "ψ", "omega": "ω", "Omega": "Ω",
    "infty": "∞", "rightarrow": "→", "leftarrow": "←", "to": "→",
    "leq": "≤", "geq": "≥", "neq": "≠", "approx": "≈", "sim": "∼",
    "star": "★", "dagger": "†", "ddagger": "‡", "circ": "∘", "downarrow": "↓",
    "uparrow": "↑", "prime": "′", "degree": "°",
}

_SYMBOL_REPLACEMENTS = [
    (r"\\" + name + r"\b", char) for name, char in _GREEK.items()
] + [
    (r"\\%", "%"),
    (r"\\&", "&"),
    (r"\\_", "_"),
    (r"\\\$", "$"),
    (r"\\#", "#"),
    (r"\\pm\b", "±"),
    (r"\\times\b", "×"),
    (r"\\cdot\b", "·"),
    (r"\\ldots\b", "…"),
    (r"\\dots\b", "…"),
    (r"\\,", " "),
    (r"\\;", " "),
    (r"\\:", " "),
    (r"\\!", ""),
    (r"\\ ", " "),
    (r"~", " "),
    (r"---", "—"),
    (r"--", "–"),
]


@dataclass
class Command:
    """A located LaTeX command with its brace-delimited arguments."""

    name: str
    args: list[str]
    start: int
    end: int


def find_matching_brace(text: str, open_index: int) -> int:
    """Return the index of the `}` matching the `{` at `open_index`.

    Raises ValueError if unbalanced, which callers treat as malformed markup
    rather than guessing.
    """
    if open_index >= len(text) or text[open_index] != "{":
        raise ValueError(f"expected '{{' at index {open_index}")
    depth = 0
    i = open_index
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError(f"unbalanced brace opened at index {open_index}")


def read_args(text: str, pos: int, count: int) -> tuple[list[str], int]:
    """Read `count` brace-delimited arguments starting at `pos`.

    Optional `[...]` arguments between mandatory ones are skipped. Returns the
    arguments and the index just past the last one. Missing arguments come back
    as empty strings so a truncated command degrades instead of exploding.
    """
    args: list[str] = []
    i = pos
    for _ in range(count):
        while i < len(text) and text[i] in " \t\n":
            i += 1
        if i < len(text) and text[i] == "[":
            depth = 0
            while i < len(text):
                if text[i] == "[":
                    depth += 1
                elif text[i] == "]":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            while i < len(text) and text[i] in " \t\n":
                i += 1
        if i < len(text) and text[i] == "{":
            try:
                close = find_matching_brace(text, i)
            except ValueError:
                args.append(text[i + 1 :])
                return args, len(text)
            args.append(text[i + 1 : close])
            i = close + 1
        else:
            args.append("")
    return args, i


def find_command(text: str, name: str, arg_count: int, start: int = 0) -> Command | None:
    """Find the first `\\name` with `arg_count` arguments at or after `start`."""
    pattern = re.compile(r"\\" + re.escape(name) + r"\b\*?")
    for match in pattern.finditer(text, start):
        args, end = read_args(text, match.end(), arg_count)
        return Command(name=name, args=args, start=match.start(), end=end)
    return None


def find_all_commands(text: str, name: str, arg_count: int) -> list[Command]:
    out: list[Command] = []
    pattern = re.compile(r"\\" + re.escape(name) + r"\b\*?")
    for match in pattern.finditer(text):
        args, end = read_args(text, match.end(), arg_count)
        out.append(Command(name=name, args=args, start=match.start(), end=end))
    return out


def split_top_level(text: str, delimiter: str) -> list[str]:
    """Split on `delimiter` at brace depth 0 and outside math mode.

    `&` inside `\\multicolumn{2}{c}{a & b}` or inside `$...$` stays put, and
    `\\&` is never a delimiter.
    """
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    in_math = False
    i = 0
    dlen = len(delimiter)
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            buf.append(text[i : i + 2])
            i += 2
            continue
        if ch == "$":
            # `$$` toggles display math; treat both as one math boundary.
            if text.startswith("$$", i):
                in_math = not in_math
                buf.append("$$")
                i += 2
                continue
            in_math = not in_math
            buf.append(ch)
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if depth == 0 and not in_math and text.startswith(delimiter, i):
            parts.append("".join(buf))
            buf = []
            i += dlen
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def split_rows(body: str) -> list[str]:
    r"""Split a tabular body into rows on top-level `\\`.

    `\\` is also the escape for a literal backslash, but inside tabular bodies a
    row break is what it always means in practice.
    """
    rows: list[str] = []
    buf: list[str] = []
    depth = 0
    in_math = False
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "$":
            if body.startswith("$$", i):
                in_math = not in_math
                buf.append("$$")
                i += 2
                continue
            in_math = not in_math
            buf.append(ch)
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "\\" and depth == 0 and not in_math:
            if body.startswith("\\\\", i):
                rows.append("".join(buf))
                buf = []
                i += 2
                # Consume an optional `[2pt]` spacing argument, but leave any
                # whitespace alone when there is none — it belongs to the next row.
                probe = i
                while probe < len(body) and body[probe] in " \t\n":
                    probe += 1
                if probe < len(body) and body[probe] == "[":
                    close = body.find("]", probe)
                    if close != -1:
                        i = close + 1
                continue
            # Any other command: copy it whole so `\textbf` is not mistaken
            # for a row break.
            buf.append(body[i : i + 2])
            i += 2
            continue
        buf.append(ch)
        i += 1
    rows.append("".join(buf))
    return rows


def strip_comments(text: str) -> str:
    """Remove `%` comments, honouring `\\%`."""
    out: list[str] = []
    for line in text.split("\n"):
        i = 0
        cut = None
        while i < len(line):
            if line[i] == "\\":
                i += 2
                continue
            if line[i] == "%":
                cut = i
                break
            i += 1
        out.append(line if cut is None else line[:cut])
    return "\n".join(out)


def collect_emphasis(text: str) -> list[str]:
    """Return the emphasis styles applied anywhere in a cell, in a stable order."""
    found: list[str] = []
    for name, style in _EMPHASIS.items():
        if re.search(r"\\" + name + r"\b", text) and style not in found:
            found.append(style)
    return sorted(found)


def flatten(text: str) -> str:
    """Reduce cell markup to readable plain text.

    Formatting commands unwrap to their contents, symbol macros become the
    characters they render as, and everything left over is dropped. The result
    is what a reader would see, which is what a cell address should contain.
    """
    text = _PARTIAL_RULE.sub(" ", text)
    for name, arg_count in _DROP_WITH_ARG:
        while True:
            cmd = find_command(text, name, arg_count)
            if cmd is None:
                break
            text = text[: cmd.start] + " " + text[cmd.end :]
    for name, leading in _UNWRAP_LAST:
        while True:
            cmd = find_command(text, name, leading + 1)
            if cmd is None:
                break
            text = text[: cmd.start] + cmd.args[-1] + text[cmd.end :]
    text = _NOISE.sub(" ", text)
    prev = None
    # Unwrap transparent commands repeatedly: `\textbf{\texttt{x}}` needs two passes.
    while prev != text:
        prev = text
        for name in _TRANSPARENT:
            while True:
                cmd = find_command(text, name, 1)
                if cmd is None:
                    break
                text = text[: cmd.start] + cmd.args[0] + text[cmd.end :]
    # Bare switches like `{\bf x}` leave their argument behind once removed.
    text = re.sub(r"\\(?:bf|it|sf|tt|rm|bfseries|itshape|ttfamily|sc)\b", " ", text)
    for pattern, replacement in _SYMBOL_REPLACEMENTS:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"\$+", " ", text)
    text = re.sub(r"\\[a-zA-Z@]+\*?", " ", text)  # any surviving command
    text = text.replace("{", " ").replace("}", " ")
    return re.sub(r"\s+", " ", text).strip()
