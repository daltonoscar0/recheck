"""Acquire the target repo read-only and pin it to a commit.

The source path is never written to. A local repo is copied out with
`git archive HEAD`, which reads the object store and emits only tracked files —
so a 1.9 GB `.venv` sitting beside the code never enters the workdir, and no
`.git` comes along to be accidentally committed to.

A report that cannot name a commit is not worth much: "we reran it" means
nothing if the reader cannot check out what we ran. So a dirty local tree is
refused by default rather than silently pinned to the last commit.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: Long enough to be unambiguous in every repo anyone will point this at.
SHORT_COMMIT_LENGTH = 7


class RepoError(RuntimeError):
    """Raised when the target repo cannot be acquired at a nameable commit."""


@dataclass(frozen=True)
class AcquiredRepo:
    """A read-only copy of the target repo, pinned to one commit."""

    spec: str
    """What the user asked for: a git URL or a local path."""

    path: Path
    """The workdir copy. Everything downstream reads and writes here."""

    commit: str
    """Short commit hash, or `"unknown"` when acquisition was forced past a
    repo with no history."""

    commit_full: str
    origin: Path | None = None
    """Local source directory, when the spec was a path rather than a URL."""

    def to_run_fields(self) -> dict[str, str]:
        """The subset of acquisition that belongs in `Results.run`."""
        fields = {"repo": self.spec, "commit": self.commit}
        if self.commit_full and self.commit_full != self.commit:
            fields["commit_full"] = self.commit_full
        return fields


def _git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:  # pragma: no cover - git missing is environmental
        raise RepoError(f"git is not available: {exc}") from exc


def is_git_repo(path: Path) -> bool:
    result = _git(["rev-parse", "--git-dir"], cwd=path)
    return result.returncode == 0


def dirty_paths(path: Path) -> list[str]:
    """Paths git considers modified, staged, or untracked-and-not-ignored."""
    result = _git(["status", "--porcelain"], cwd=path)
    if result.returncode != 0:
        return []
    return [line[3:].strip() for line in result.stdout.splitlines() if line.strip()]


def resolve_commit(path: Path) -> tuple[str, str]:
    result = _git(["rev-parse", "HEAD"], cwd=path)
    if result.returncode != 0:
        raise RepoError(
            f"{path} has no commits to pin to (git rev-parse HEAD: "
            f"{result.stderr.strip() or 'failed'})"
        )
    full = result.stdout.strip()
    return full[:SHORT_COMMIT_LENGTH], full


def _export_tracked(source: Path, dest: Path) -> None:
    """Copy the tracked tree at HEAD into `dest` without touching `source`."""
    dest.mkdir(parents=True, exist_ok=True)
    with subprocess.Popen(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=str(source),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as producer:
        assert producer.stdout is not None
        extract = subprocess.run(
            ["tar", "-x", "-C", str(dest)],
            stdin=producer.stdout,
            capture_output=True,
            check=False,
        )
        producer.stdout.close()
        stderr = producer.stderr.read().decode() if producer.stderr else ""
    if producer.returncode != 0:  # pragma: no cover - archive fails only on odd repos
        raise RepoError(f"could not export {source} at HEAD: {stderr.strip()}")
    if extract.returncode != 0:  # pragma: no cover - tar failure is environmental
        raise RepoError(f"could not unpack {source} into {dest}: {extract.stderr.decode().strip()}")


def _copy_untracked(source: Path, dest: Path) -> None:
    """Fallback copy for a directory that is not a git repo at all."""

    def ignored(directory: str, names: list[str]) -> set[str]:
        del directory
        return {n for n in names if n in _NEVER_COPY}

    shutil.copytree(source, dest, ignore=ignored, dirs_exist_ok=True, symlinks=True)


#: Directories that are never worth copying and are sometimes enormous.
_NEVER_COPY = frozenset(
    {".git", ".venv", "venv", "env", "__pycache__", "node_modules", ".mypy_cache",
     ".pytest_cache", ".ruff_cache", ".tox", ".DS_Store"}
)


def acquire(
    spec: str,
    dest: Path,
    *,
    allow_dirty: bool = False,
    clone_depth: int = 1,
) -> AcquiredRepo:
    """Put a pinned, read-only copy of `spec` under `dest`.

    `spec` is a local path or anything git can clone. The source is only ever
    read; `dest` is the workdir everything downstream is free to mutate.
    """
    local = Path(spec).expanduser()
    if local.exists():
        return _acquire_local(spec, local.resolve(), dest, allow_dirty=allow_dirty)
    return _acquire_remote(spec, dest, clone_depth=clone_depth)


def _acquire_local(spec: str, source: Path, dest: Path, *, allow_dirty: bool) -> AcquiredRepo:
    if not source.is_dir():
        raise RepoError(f"--repo {spec} is a file; expected a directory or a git URL")

    if not is_git_repo(source):
        if not allow_dirty:
            raise RepoError(
                f"{source} is not a git repository, so the report could not name a commit "
                f"anyone else could check out; pass --allow-dirty to run against it anyway"
            )
        _copy_untracked(source, dest)
        return AcquiredRepo(spec=spec, path=dest, commit="unknown", commit_full="", origin=source)

    dirty = dirty_paths(source)
    if dirty and not allow_dirty:
        shown = ", ".join(dirty[:5]) + (f", and {len(dirty) - 5} more" if len(dirty) > 5 else "")
        raise RepoError(
            f"{source} has {len(dirty)} uncommitted change(s) ({shown}); the report must name a "
            f"commit someone else can check out. Commit them, or pass --allow-dirty."
        )

    commit, commit_full = resolve_commit(source)
    _export_tracked(source, dest)
    if dirty:
        # --allow-dirty was given: the tracked tree at HEAD is what we ran, and
        # the note downstream says the working copy differed.
        _copy_untracked(source, dest)
    return AcquiredRepo(
        spec=spec, path=dest, commit=commit, commit_full=commit_full, origin=source
    )


def _acquire_remote(spec: str, dest: Path, *, clone_depth: int) -> AcquiredRepo:
    dest.mkdir(parents=True, exist_ok=True)
    args = ["clone", "--quiet"]
    if clone_depth:
        args += ["--depth", str(clone_depth)]
    args += [spec, str(dest)]
    result = _git(args)
    if result.returncode != 0:
        raise RepoError(f"could not clone {spec}: {result.stderr.strip() or 'git clone failed'}")
    commit, commit_full = resolve_commit(dest)
    return AcquiredRepo(spec=spec, path=dest, commit=commit, commit_full=commit_full)
