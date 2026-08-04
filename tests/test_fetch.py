from __future__ import annotations

import gzip
import io
import tarfile

import pytest

from recheck.paper.fetch import FetchError, fetch, parse_arxiv_id, unpack


def make_tar(files: dict[str, str], compress: bool = False) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    raw = buffer.getvalue()
    return gzip.compress(raw) if compress else raw


class TestIdParsing:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("https://arxiv.org/abs/2401.12345", "2401.12345"),
            ("https://arxiv.org/abs/2401.12345v2", "2401.12345v2"),
            ("http://arxiv.org/pdf/2401.12345", "2401.12345"),
            ("https://arxiv.org/pdf/2401.12345.pdf", "2401.12345"),
            ("arxiv.org/e-print/2401.12345", "2401.12345"),
            ("2401.12345", "2401.12345"),
            ("cs.CL/0501001", "cs.CL/0501001"),
        ],
    )
    def test_parses(self, given: str, expected: str) -> None:
        assert parse_arxiv_id(given) == expected

    def test_rejects_nonsense(self) -> None:
        with pytest.raises(FetchError, match="could not parse"):
            parse_arxiv_id("not a paper")


class TestUnpacking:
    def test_plain_tar(self) -> None:
        source = unpack(make_tar({"main.tex": r"\documentclass{article}"}))
        assert source.files["main.tex"].startswith(r"\documentclass")

    def test_gzipped_tar(self) -> None:
        source = unpack(make_tar({"main.tex": "hello"}, compress=True))
        assert source.files["main.tex"] == "hello"

    def test_bare_gzipped_tex(self) -> None:
        source = unpack(gzip.compress(rb"\documentclass{article}"))
        assert "main.tex" in source.files

    def test_uncompressed_single_tex(self) -> None:
        source = unpack(rb"\documentclass{article}")
        assert source.files["main.tex"].startswith(r"\documentclass")

    def test_tex_files_filters_by_suffix(self) -> None:
        source = unpack(make_tar({"main.tex": "a", "refs.bib": "b", "fig.pdf_tex": "c"}))
        assert set(source.tex_files()) == {"main.tex"}

    def test_binary_members_are_skipped(self) -> None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for name, data in [("main.tex", b"text"), ("fig.png", b"\x89PNG\x00\x00binary")]:
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        source = unpack(buffer.getvalue())
        assert set(source.files) == {"main.tex"}

    def test_path_traversal_members_are_rejected(self) -> None:
        source = unpack(make_tar({"../escape.tex": "bad", "main.tex": "good"}))
        assert set(source.files) == {"main.tex"}

    def test_empty_archive_raises(self) -> None:
        with pytest.raises(FetchError, match="no readable text files"):
            unpack(make_tar({}))

    def test_writes_to_workdir(self, tmp_path) -> None:
        unpack(make_tar({"sections/intro.tex": "prose"}), workdir=tmp_path)
        assert (tmp_path / "sections" / "intro.tex").read_text() == "prose"


@pytest.mark.network
class TestLiveFetch:
    """Exercised only with `pytest -m network`; never faked when offline."""

    def test_downloads_a_real_eprint(self) -> None:
        source = fetch("1706.03762")  # Attention Is All You Need
        assert source.tex_files()
