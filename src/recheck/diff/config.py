"""Tolerance configuration loading.

A per-table band is the difference between a useful report and a noisy one: a
BLEU table and a perplexity table in the same paper do not deserve the same
sensitivity. The global `--tolerance` sets the default; this file lets a
`recheck.toml` override it per table.

```toml
[tolerance]
relative = 0.02
absolute = 0.1

[tolerance.tables."tab:npz"]
relative = 0.005

[tolerance.tables."table-2"]
absolute = 2.0
yellow_multiplier = 2.0
```

Tables are keyed by the id `recheck extract` assigns: the LaTeX `\\label` when
there is one, otherwise `table-N`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .engine import Tolerance, TolerancePolicy

CONFIG_FILENAME = "recheck.toml"

_KEYS = {
    "relative": "relative",
    "rel": "relative",
    "absolute": "absolute",
    "abs": "absolute",
    "yellow_multiplier": "yellow_multiplier",
    "yellow": "yellow_multiplier",
}


class ConfigError(ValueError):
    """Raised when a tolerance config cannot be understood."""


def _tolerance_from_table(raw: dict[str, Any], where: str, base: Tolerance) -> Tolerance:
    """Build a Tolerance from a TOML table, inheriting unset fields from `base`."""
    values = {
        "relative": base.relative,
        "absolute": base.absolute,
        "yellow_multiplier": base.yellow_multiplier,
    }
    for key, value in raw.items():
        if key == "tables":
            continue
        field = _KEYS.get(key.lower())
        if field is None:
            known = ", ".join(sorted(set(_KEYS)))
            raise ConfigError(f"{where}: unknown tolerance key {key!r}; expected one of: {known}")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ConfigError(f"{where}: {key} must be a number, got {value!r}")
        values[field] = float(value)
    return Tolerance(**values)


def parse_tolerance_config(data: dict[str, Any], where: str = "config") -> TolerancePolicy:
    """Turn parsed TOML into a policy. Accepts the document or its [tolerance] table."""
    section = data.get("tolerance", data)
    if not isinstance(section, dict):
        raise ConfigError(f"{where}: [tolerance] must be a table")

    default = _tolerance_from_table(section, f"{where} [tolerance]", Tolerance())

    per_table: dict[str, Tolerance] = {}
    tables = section.get("tables", {})
    if not isinstance(tables, dict):
        raise ConfigError(f"{where}: [tolerance.tables] must be a table")
    for table_id, raw in tables.items():
        if not isinstance(raw, dict):
            raise ConfigError(
                f"{where}: [tolerance.tables.{table_id!r}] must be a table of settings"
            )
        # Per-table settings inherit the default, so a table overriding only
        # `relative` keeps the configured absolute floor.
        per_table[table_id] = _tolerance_from_table(
            raw, f"{where} [tolerance.tables.{table_id}]", default
        )

    return TolerancePolicy(default=default, per_table=per_table)


def load_tolerance_config(path: Path) -> TolerancePolicy:
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"tolerance config not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    return parse_tolerance_config(data, where=str(path))


def discover_config(start: Path) -> Path | None:
    """Find `recheck.toml` in `start` or any parent, nearest first.

    Returns None rather than raising: running without a config is the norm.
    """
    start = start.resolve()
    candidates = [start, *start.parents] if start.is_dir() else [start.parent, *start.parents]
    for directory in candidates:
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def resolve_policy(
    config_path: Path | None,
    cli_tolerance: Tolerance | None,
    search_from: Path | None = None,
) -> tuple[TolerancePolicy, Path | None]:
    """Combine config file and CLI flag into one policy.

    Precedence, strongest first: `--tolerance` on the command line, then the
    config's `[tolerance]` default, then the built-in default. Per-table entries
    always apply to their own table — an explicit per-table band is a more
    specific statement than a global flag.
    """
    used: Path | None = None
    if config_path is not None:
        policy = load_tolerance_config(config_path)
        used = config_path
    else:
        discovered = discover_config(search_from) if search_from is not None else None
        if discovered is not None:
            policy = load_tolerance_config(discovered)
            used = discovered
        else:
            policy = TolerancePolicy()

    if cli_tolerance is not None:
        policy = TolerancePolicy(default=cli_tolerance, per_table=policy.per_table)
    return policy, used
