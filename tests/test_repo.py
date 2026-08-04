"""Acquisition must be read-only, and must be able to name a commit."""

from __future__ import annotations

import subprocess

import pytest

from conftest import GIT_IDENTITY, make_git_repo, write_files
from recheck.repo import RepoError, acquire, dirty_paths, is_git_repo, resolve_commit


class TestAcquireLocal:
    def test_copies_the_tracked_tree_and_pins_the_commit(self, tmp_path) -> None:
        source = make_git_repo(tmp_path / "source", {"a.py": "print(1)\n", "data/x.csv": "a\n1\n"})
        acquired = acquire(str(source), tmp_path / "work")

        assert (tmp_path / "work" / "a.py").read_text() == "print(1)\n"
        assert (tmp_path / "work" / "data" / "x.csv").exists()
        expected, _ = resolve_commit(source)
        assert acquired.commit == expected
        assert len(acquired.commit_full) == 40
        assert acquired.origin == source.resolve()

    def test_does_not_mutate_the_source(self, tmp_path) -> None:
        source = make_git_repo(tmp_path / "source", {"a.py": "print(1)\n"})
        before = sorted(p.name for p in source.rglob("*"))
        acquire(str(source), tmp_path / "work")
        assert sorted(p.name for p in source.rglob("*")) == before
        assert dirty_paths(source) == []

    def test_leaves_untracked_and_ignored_files_behind(self, tmp_path) -> None:
        source = make_git_repo(
            tmp_path / "source", {"a.py": "print(1)\n", ".gitignore": ".venv/\n"}
        )
        write_files(source / ".venv", {"huge.bin": "x" * 1024})

        acquire(str(source), tmp_path / "work")

        assert not (tmp_path / "work" / ".venv").exists()
        assert not (tmp_path / "work" / ".git").exists()

    def test_refuses_a_dirty_tree_and_names_the_files(self, tmp_path) -> None:
        source = make_git_repo(tmp_path / "source", {"a.py": "print(1)\n"})
        (source / "a.py").write_text("print(2)\n")

        with pytest.raises(RepoError) as caught:
            acquire(str(source), tmp_path / "work")

        message = str(caught.value)
        assert "a.py" in message
        assert "check out" in message
        assert "--allow-dirty" in message

    def test_allow_dirty_takes_the_working_copy(self, tmp_path) -> None:
        source = make_git_repo(tmp_path / "source", {"a.py": "print(1)\n"})
        (source / "a.py").write_text("print(2)\n")

        acquired = acquire(str(source), tmp_path / "work", allow_dirty=True)

        assert (tmp_path / "work" / "a.py").read_text() == "print(2)\n"
        assert acquired.commit != "unknown"

    def test_refuses_a_directory_that_is_not_a_repo(self, tmp_path) -> None:
        source = write_files(tmp_path / "plain", {"a.py": "print(1)\n"})
        with pytest.raises(RepoError) as caught:
            acquire(str(source), tmp_path / "work")
        assert "not a git repository" in str(caught.value)
        assert "commit" in str(caught.value)

    def test_allow_dirty_copies_a_plain_directory(self, tmp_path) -> None:
        source = write_files(tmp_path / "plain", {"a.py": "print(1)\n"})
        acquired = acquire(str(source), tmp_path / "work", allow_dirty=True)
        assert acquired.commit == "unknown"
        assert (tmp_path / "work" / "a.py").exists()

    def test_refuses_a_file(self, tmp_path) -> None:
        target = tmp_path / "a.py"
        target.write_text("print(1)\n")
        with pytest.raises(RepoError):
            acquire(str(target), tmp_path / "work")

    def test_repo_with_no_commits_cannot_be_pinned(self, tmp_path) -> None:
        source = make_git_repo(tmp_path / "source", {"a.py": "x\n"}, commit=False)
        with pytest.raises(RepoError) as caught:
            acquire(str(source), tmp_path / "work", allow_dirty=True)
        assert "no commits" in str(caught.value)


class TestAcquireRemote:
    def test_unreachable_url_reports_git_error(self, tmp_path) -> None:
        with pytest.raises(RepoError) as caught:
            acquire("file:///definitely/not/a/repo", tmp_path / "work")
        assert "could not clone" in str(caught.value)

    def test_clones_a_local_git_url(self, tmp_path) -> None:
        source = make_git_repo(tmp_path / "source", {"a.py": "print(1)\n"})
        acquired = acquire(f"file://{source}", tmp_path / "work")
        assert (tmp_path / "work" / "a.py").exists()
        assert acquired.origin is None
        assert acquired.commit != "unknown"


class TestHelpers:
    def test_is_git_repo(self, tmp_path) -> None:
        assert is_git_repo(make_git_repo(tmp_path / "yes", {"a": "1"}))
        assert not is_git_repo(write_files(tmp_path / "no", {"a": "1"}))

    def test_dirty_paths_lists_untracked_files(self, tmp_path) -> None:
        source = make_git_repo(tmp_path / "source", {"a.py": "1\n"})
        (source / "new.txt").write_text("hello\n")
        assert "new.txt" in dirty_paths(source)

    def test_run_fields_include_the_full_commit(self, tmp_path) -> None:
        source = make_git_repo(tmp_path / "source", {"a.py": "1\n"})
        fields = acquire(str(source), tmp_path / "work").to_run_fields()
        assert fields["commit_full"].startswith(fields["commit"])

    def test_amended_history_changes_the_pin(self, tmp_path) -> None:
        source = make_git_repo(tmp_path / "source", {"a.py": "1\n"})
        first = acquire(str(source), tmp_path / "one").commit
        (source / "a.py").write_text("2\n")
        subprocess.run(["git", "-C", str(source), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(source), *GIT_IDENTITY, "commit", "-q", "-m", "second"], check=True
        )
        assert acquire(str(source), tmp_path / "two").commit != first


class TestPortableProvenance:
    def test_a_home_path_is_recorded_relative(self, tmp_path, monkeypatch) -> None:
        from recheck.repo.acquire import _portable_spec

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        (tmp_path / "paper-code").mkdir()
        assert _portable_spec(str(tmp_path / "paper-code")) == "~/paper-code"

    def test_a_git_url_is_left_alone(self) -> None:
        from recheck.repo.acquire import _portable_spec

        for url in ("https://github.com/a/b", "git@github.com:a/b.git"):
            assert _portable_spec(url) == url

    def test_a_path_outside_home_is_left_alone(self, tmp_path, monkeypatch) -> None:
        from recheck.repo.acquire import _portable_spec

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        assert _portable_spec(str(outside)) == str(outside)
