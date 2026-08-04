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
    "text", "mbox", "operatorname", "mathcal", "mathbb", "mathfrak", "textnormal",
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
# Citation keys are machinery too: a row label reading "ByteNet NalBytenet2017"
# is worse than "ByteNet", and the key is never part of what the paper shows.
_DROP_WITH_ARG = (
    ("label", 1), ("vspace", 1), ("hspace", 1), ("rule", 2),
    ("cite", 1), ("citep", 1), ("citet", 1), ("citealp", 1), ("citealt", 1),
    ("citeauthor", 1), ("citeyear", 1), ("footnote", 1), ("footnotemark", 0),
)

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

# `(?![a-zA-Z])` rather than `\b`: a trailing `_` is a word character, so `\b`
# would fail to match "\epsilon_{ls}" and the symbol would vanish.
_CMD_END = r"(?![a-zA-Z])"

_SYMBOL_REPLACEMENTS = [
    (r"\\" + name + _CMD_END, char) for name, char in _GREEK.items()
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


_NEWCOMMAND = re.compile(r"\\(?:re)?newcommand\*?\s*\{?\s*\\([a-zA-Z@]+)\s*\}?")
_DEF = re.compile(r"\\def\s*\\([a-zA-Z@]+)\s*\{")

# Never shadow the markup the grid builder depends on, however a paper redefines it.
_PROTECTED = frozenset({
    "multicolumn", "multirow", "begin", "end", "caption", "label", "input", "include",
    "hline", "toprule", "midrule", "bottomrule", "cmidrule", "cline", "documentclass",
})


def collect_macros(text: str) -> dict[str, tuple[int, str]]:
    r"""Collect user-defined `\newcommand` / `\def` macros as name -> (arity, body)."""
    macros: dict[str, tuple[int, str]] = {}

    for match in _NEWCOMMAND.finditer(text):
        name = match.group(1)
        if name in _PROTECTED:
            continue
        i = match.end()
        while i < len(text) and text[i] in " \t\n":
            i += 1
        arity = 0
        if i < len(text) and text[i] == "[":
            close = text.find("]", i)
            if close != -1:
                try:
                    arity = int(text[i + 1 : close].strip())
                except ValueError:
                    arity = 0
                i = close + 1
        # An optional-argument default follows; skipping it means such macros
        # expand with the default missing, which beats not expanding at all.
        while i < len(text) and text[i] in " \t\n":
            i += 1
        if i < len(text) and text[i] == "[":
            close = text.find("]", i)
            if close != -1:
                i = close + 1
        while i < len(text) and text[i] in " \t\n":
            i += 1
        if i < len(text) and text[i] == "{":
            try:
                close = find_matching_brace(text, i)
            except ValueError:
                continue
            macros[name] = (arity, text[i + 1 : close])

    for match in _DEF.finditer(text):
        name = match.group(1)
        if name in _PROTECTED or name in macros:
            continue
        brace = match.end() - 1
        try:
            close = find_matching_brace(text, brace)
        except ValueError:
            continue
        macros[name] = (0, text[brace + 1 : close])

    return macros


def expand_macros(text: str, macros: dict[str, tuple[int, str]] | None = None,
                  max_passes: int = 8) -> str:
    r"""Expand user-defined macros so `$\dmodel$` becomes readable header text.

    Bounded rather than fixed-point: a self-referential macro should degrade to
    partially-expanded text, not hang the extractor.
    """
    macros = collect_macros(text) if macros is None else macros
    if not macros:
        return text

    for _ in range(max_passes):
        changed = False
        for name, (arity, body) in macros.items():
            pattern = re.compile(r"\\" + re.escape(name) + r"(?![a-zA-Z@])")
            while True:
                match = pattern.search(text)
                if match is None:
                    break
                if arity:
                    args, end = read_args(text, match.end(), arity)
                    expansion = body
                    for index, value in enumerate(args, start=1):
                        expansion = expansion.replace(f"#{index}", value)
                else:
                    expansion, end = body, match.end()
                if expansion == text[match.start() : end]:
                    break  # self-referential; leave it alone
                text = text[: match.start()] + expansion + text[end:]
                changed = True
        if not changed:
            break
    return text


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
    # Collapse sub/superscript groups before braces become spaces, so
    # `d_{\text{model}}` reads as "d_model" rather than "d_ model".
    text = re.sub(
        r"([_^])\s*\{\s*([^{}]*?)\s*\}",
        lambda m: m.group(1) + re.sub(r"\s+", "", m.group(2)),
        text,
    )
    text = text.replace("{", " ").replace("}", " ")
    return re.sub(r"\s+", " ", text).strip()
