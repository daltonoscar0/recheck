"""On-disk cache of mappings, keyed by repo commit.

Mapping is the stage where judgment lives, so it is also the stage a reviewer
most wants to read and disagree with. Writing the plan out keyed by commit
makes reruns cheap *and* makes the routing reviewable: `cat` the file and you
can see exactly which column of which CSV a number came from.

A cache entry is only reused when the commit, the mapper, and the plan format
all match. Anything else is a miss, because a stale plan is worse than no plan.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .plan import PLAN_VERSION, RepoPlan


def default_cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(root) / "recheck" / "plans"


def cache_path(directory: Path, commit: str, mapper: str) -> Path:
    return directory / f"{commit}.{mapper}.json"


def load(directory: Path, commit: str, mapper: str) -> RepoPlan | None:
    path = cache_path(directory, commit, mapper)
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("plan_version") != PLAN_VERSION:
        return None
    if data.get("commit") != commit or data.get("mapper") != mapper:
        return None
    try:
        return RepoPlan.from_dict(data)
    except (KeyError, ValueError):
        return None


def store(directory: Path, plan: RepoPlan) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = cache_path(directory, plan.commit, plan.mapper)
    path.write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False) + "\n")
    return path
