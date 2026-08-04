"""On-disk cache of mappings, keyed by repo commit.

Mapping is the stage where judgment lives, so it is also the stage a reviewer
most wants to read and disagree with. Writing the plan out keyed by commit
makes reruns cheap *and* makes the routing reviewable: `cat` the file and you
can see exactly which column of which CSV a number came from.

A cache entry is only reused when the commit, the mapper, and the plan format
all match. Anything else is a miss, because a stale plan is worse than no plan.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..schema import Paper
from .plan import PLAN_VERSION, RepoPlan


def default_cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(root) / "recheck" / "plans"


def paper_key(paper: Paper) -> str:
    """A short fingerprint of what the plan was computed for.

    A plan maps *this paper's* cells onto a repo. Keyed on the repo alone, a
    second paper run against the same repo silently loaded the first paper's
    plan. `commit` is also "unknown" for any non-git directory, so two unrelated
    folders collided on the same key.
    """
    digest = hashlib.sha256()
    for table in paper.tables:
        digest.update(table.id.encode())
        for cell in table.cells:
            digest.update(cell.address.encode())
    return digest.hexdigest()[:12]


def cache_path(directory: Path, commit: str, mapper: str, paper: str) -> Path:
    return directory / f"{commit}.{mapper}.{paper}.json"


def load(directory: Path, commit: str, mapper: str, paper: str) -> RepoPlan | None:
    path = cache_path(directory, commit, mapper, paper)
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("plan_version") != PLAN_VERSION:
        return None
    if data.get("commit") != commit or data.get("mapper") != mapper:
        return None
    if data.get("paper_key") != paper:
        return None
    try:
        return RepoPlan.from_dict(data)
    except (KeyError, ValueError):
        return None


def store(directory: Path, plan: RepoPlan, paper: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = cache_path(directory, plan.commit, plan.mapper, paper)
    payload = plan.to_dict()
    payload["paper_key"] = paper
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path
