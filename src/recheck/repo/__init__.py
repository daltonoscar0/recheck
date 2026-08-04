"""Repo side: acquire the target repository read-only and pin it to a commit."""

from .acquire import AcquiredRepo, RepoError, acquire, dirty_paths, is_git_repo, resolve_commit

__all__ = [
    "AcquiredRepo",
    "RepoError",
    "acquire",
    "dirty_paths",
    "is_git_repo",
    "resolve_commit",
]
