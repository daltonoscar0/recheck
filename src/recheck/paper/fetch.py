"""Retrieval and unpacking of arXiv e-print source.

arXiv serves e-prints from a single endpoint whose payload may be a gzipped tar,
a bare gzipped .tex, or an uncompressed tar. Content-type is unreliable, so the
format is sniffed from magic bytes.
"""

from __future__ import annotations

import gzip
import io
import re
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

EPRINT_URL = "https://arxiv.org/e-print/{arxiv_id}"
USER_AGENT = "recheck/0.1 (reproduction checker; +https://github.com/daltonoscar0/recheck)"

_ID_PATTERNS = (
    re.compile(r"arxiv\.org/(?:abs|pdf|e-print)/([^\s?#]+)", re.I),
    re.compile(r"^(\d{4}\.\d{4,5}(?:v\d+)?)$"),
    re.compile(r"^([a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)$", re.I),
)

TEX_SUFFIXES = (".tex", ".ltx")


class FetchError(RuntimeError):
    """Raised when source cannot be retrieved or understood."""


@dataclass
class Source:
    """An unpacked e-print: every text file found, keyed by relative path."""

    arxiv_id: str
    files: dict[str, str]
    workdir: Path | None = None

    def tex_files(self) -> dict[str, str]:
        return {n: t for n, t in self.files.items() if n.lower().endswith(TEX_SUFFIXES)}


def parse_arxiv_id(url_or_id: str) -> str:
    """Extract a bare arXiv identifier from a URL or an already-bare ID."""
    candidate = url_or_id.strip()
    for pattern in _ID_PATTERNS:
        match = pattern.search(candidate)
        if match:
            found = match.group(1)
            return found[:-4] if found.lower().endswith(".pdf") else found
    raise FetchError(f"could not parse an arXiv identifier from {url_or_id!r}")


def download(arxiv_id: str, timeout: float = 30.0) -> bytes:
    request = urllib.request.Request(
        EPRINT_URL.format(arxiv_id=arxiv_id), headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the user
        raise FetchError(f"failed to download e-print {arxiv_id}: {exc}") from exc


def unpack(payload: bytes, arxiv_id: str = "", workdir: Path | None = None) -> Source:
    """Turn a raw e-print payload into a `Source`, sniffing the container format."""
    if payload[:2] == b"\x1f\x8b":
        try:
            payload = gzip.decompress(payload)
        except OSError as exc:
            raise FetchError(f"payload claimed gzip but did not decompress: {exc}") from exc

    files: dict[str, str] = {}
    if payload[257:262] == b"ustar" or _looks_like_tar(payload):
        with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                # Check the name as the archive states it: stripping first would
                # quietly turn "../escape.tex" into an innocent-looking path.
                if _unsafe_path(member.name):
                    continue
                name = member.name[2:] if member.name.startswith("./") else member.name
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                decoded = _decode(handle.read())
                if decoded is not None:
                    files[name] = decoded
    else:
        decoded = _decode(payload)
        if decoded is None:
            raise FetchError("e-print payload is neither a tar archive nor decodable text")
        files["main.tex"] = decoded

    if not files:
        raise FetchError("e-print archive contained no readable text files")

    if workdir is not None:
        workdir.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            target = workdir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

    return Source(arxiv_id=arxiv_id, files=files, workdir=workdir)


def fetch(url_or_id: str, workdir: Path | None = None, timeout: float = 30.0) -> Source:
    arxiv_id = parse_arxiv_id(url_or_id)
    return unpack(download(arxiv_id, timeout=timeout), arxiv_id=arxiv_id, workdir=workdir)


def load_local(path: Path) -> Source:
    """Load source from a local tarball or a directory of .tex files."""
    if path.is_dir():
        files: dict[str, str] = {}
        for child in sorted(path.rglob("*")):
            if child.is_file():
                decoded = _decode(child.read_bytes())
                if decoded is not None:
                    files[str(child.relative_to(path))] = decoded
        if not files:
            raise FetchError(f"no readable text files under {path}")
        return Source(arxiv_id="", files=files, workdir=path)
    return unpack(path.read_bytes(), arxiv_id=path.stem)


def _looks_like_tar(payload: bytes) -> bool:
    if len(payload) < 512:
        return False
    try:
        with tarfile.open(fileobj=io.BytesIO(payload)):
            return True
    except tarfile.TarError:
        return False


def _unsafe_path(name: str) -> bool:
    """Reject absolute paths and traversal, which tarfile will happily honour."""
    return name.startswith("/") or ".." in Path(name).parts


def _decode(raw: bytes) -> str | None:
    for encoding in ("utf-8", "latin-1"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" in text[:4096]:
            return None  # binary (figure, font) rather than source
        return text
    return None
