"""Compute ceilings, and the ledger that enforces them.

Budgets are hard. Not "we stop when we notice", not "we warn and continue" —
a run that would breach a ceiling is refused before it starts, and a run that
breaches one mid-flight is killed. `BUDGET_EXCEEDED` with a measured number
against a stated ceiling is a real finding about a paper; quietly running for
nine hours is not.

Three ceilings, because papers fail against three different scarcities: wall
clock, GPU time, and the size of the checkpoints a table depends on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True)
class Budget:
    """The ceilings for a whole run, from the CLI flags."""

    max_hours: float = 2.0
    max_gpu_hours: float = 1.0
    max_download_mb: float = 512.0

    def to_dict(self) -> dict[str, float]:
        return {
            "max_hours": self.max_hours,
            "max_gpu_hours": self.max_gpu_hours,
            "max_download_mb": self.max_download_mb,
        }


@dataclass(frozen=True)
class CostEstimate:
    """What one entry point is predicted to cost, and why we think so."""

    wall_hours: float = 0.0
    gpu_hours: float = 0.0
    download_mb: float = 0.0
    basis: tuple[str, ...] = ()
    wall_basis: tuple[str, ...] = ()
    gpu_basis: tuple[str, ...] = ()
    download_basis: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "wall_hours": round(self.wall_hours, 4),
            "gpu_hours": round(self.gpu_hours, 4),
            "download_mb": round(self.download_mb, 1),
            "basis": list(self.basis),
        }


@dataclass
class Ledger:
    """Running total of what has been spent, against a fixed budget."""

    budget: Budget
    wall_seconds: float = 0.0
    gpu_seconds: float = 0.0
    downloaded_mb: float = 0.0
    refusals: list[str] = field(default_factory=list)

    def remaining_hours(self) -> float:
        return max(0.0, self.budget.max_hours - self.wall_seconds / SECONDS_PER_HOUR)

    def remaining_gpu_hours(self) -> float:
        return max(0.0, self.budget.max_gpu_hours - self.gpu_seconds / SECONDS_PER_HOUR)

    def remaining_download_mb(self) -> float:
        return max(0.0, self.budget.max_download_mb - self.downloaded_mb)

    def remaining_seconds(self) -> float:
        return self.remaining_hours() * SECONDS_PER_HOUR

    def spend_wall(self, seconds: float) -> None:
        self.wall_seconds += max(0.0, seconds)

    def spend_gpu(self, seconds: float) -> None:
        self.gpu_seconds += max(0.0, seconds)

    def spend_download(self, megabytes: float) -> None:
        self.downloaded_mb += max(0.0, megabytes)

    def refuse(self, estimate: CostEstimate, what: str) -> str | None:
        """Evidence for refusing this estimate, or None if it fits.

        Checked before anything starts, so a run whose *estimate alone* breaches
        a ceiling never gets to spend a second proving it.
        """
        for measured, remaining, ceiling, unit, flag, basis in (
            (estimate.download_mb, self.remaining_download_mb(),
             self.budget.max_download_mb, "MB of downloads", "--max-download-mb",
             estimate.download_basis),
            (estimate.gpu_hours, self.remaining_gpu_hours(),
             self.budget.max_gpu_hours, "GPU-hours", "--max-gpu", estimate.gpu_basis),
            (estimate.wall_hours, self.remaining_hours(),
             self.budget.max_hours, "wall-clock hours", "--max-hours", estimate.wall_basis),
        ):
            if measured > remaining:
                spent = ceiling - remaining
                already = f" ({spent:g} already spent)" if spent > 0 else ""
                reasons = basis or estimate.basis
                detail = "; ".join(reasons) if reasons else "no basis recorded"
                message = (
                    f"{what} needs an estimated {measured:g} {unit} against a {flag} ceiling of "
                    f"{ceiling:g}{already}: {detail}"
                )
                self.refusals.append(message)
                return message
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            **self.budget.to_dict(),
            "spent_hours": round(self.wall_seconds / SECONDS_PER_HOUR, 4),
            "spent_gpu_hours": round(self.gpu_seconds / SECONDS_PER_HOUR, 4),
            "downloaded_mb": round(self.downloaded_mb, 1),
        }


class Stopwatch:
    """Wall-clock timer that charges the ledger whatever it measures."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.started = 0.0
        self.elapsed = 0.0

    def __enter__(self) -> Stopwatch:
        self.started = time.monotonic()
        return self

    def __exit__(self, *_: object) -> None:
        self.elapsed = time.monotonic() - self.started
        self.ledger.spend_wall(self.elapsed)
