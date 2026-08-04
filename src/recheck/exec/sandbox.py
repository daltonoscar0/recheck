"""Where a target repo's code is allowed to run.

Two rules from CLAUDE.md hold this shape. Nothing outside the sandbox is
written, so the workdir is the only path a run can reach by relative path and
the source repo is never on the other end of one. And budgets are hard, so a
process is polled while it runs and killed at the deadline rather than being
asked politely to finish.

`Sandbox` is a protocol. `LocalSandbox` isolates by process: a scrubbed
environment (a target repo's code never sees the operator's API keys), a home
and cache redirected into the workdir, and a CPU rlimit. That is containment,
not a security boundary — a container backend plugs in at this seam and is the
first thing to add when recheck starts running code from repos nobody has read.
"""

from __future__ import annotations

import os
import resource
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

#: How often the deadline is rechecked while a run is in flight.
POLL_SECONDS = 0.1

#: How much of a run's output is kept for evidence. A traceback's last lines
#: are what names the missing package; megabytes of progress bars are not.
MAX_CAPTURED_BYTES = 64 * 1024

#: Environment a target repo's code is allowed to see.
PASSTHROUGH_ENV = (
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "SYSTEMROOT", "COMSPEC",
)

GRACE_SECONDS = 5.0


@dataclass
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def tail(self, lines: int = 12) -> str:
        """The end of the output, which is where the reason usually is."""
        text = (self.stderr or self.stdout).strip()
        return "\n".join(text.splitlines()[-lines:])


class Sandbox(Protocol):
    root: Path

    @property
    def workdir(self) -> Path: ...

    def run(self, argv: list[str], *, timeout: float, cwd: Path | None = None,
            env: dict[str, str] | None = None) -> CommandResult: ...


class LocalSandbox:
    """Process-level isolation rooted at a workdir."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("repo", "home", "tmp", "cache"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    @property
    def workdir(self) -> Path:
        return self.root / "repo"

    def environment(self) -> dict[str, str]:
        """A scrubbed environment with caches redirected inside the sandbox."""
        env = {name: os.environ[name] for name in PASSTHROUGH_ENV if name in os.environ}
        home = self.root / "home"
        cache = self.root / "cache"
        env.update(
            {
                "HOME": str(home),
                "TMPDIR": str(self.root / "tmp"),
                "XDG_CACHE_HOME": str(cache),
                "HF_HOME": str(cache / "huggingface"),
                "TORCH_HOME": str(cache / "torch"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "MPLBACKEND": "Agg",
            }
        )
        return env

    def run(
        self,
        argv: list[str],
        *,
        timeout: float,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Run a command, killing it the moment the deadline passes."""
        environment = self.environment()
        if env:
            environment.update(env)
        deadline = time.monotonic() + max(timeout, 0.0)
        started = time.monotonic()

        # Output goes to files, not pipes. A run that fills a 64 KB pipe buffer
        # while we are polling the deadline would block forever, and a budget
        # that deadlocks is not a budget.
        out_path = self.root / "tmp" / "stdout.log"
        err_path = self.root / "tmp" / "stderr.log"
        try:
            with out_path.open("w") as out, err_path.open("w") as err:
                process = subprocess.Popen(
                    argv,
                    cwd=str(cwd or self.workdir),
                    env=environment,
                    stdout=out,
                    stderr=err,
                    text=True,
                    start_new_session=True,
                    preexec_fn=_limit_factory(timeout) if os.name == "posix" else None,
                )
                timed_out = False
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        timed_out = True
                        _terminate(process)
                        break
                    time.sleep(POLL_SECONDS)
                process.wait()
        except (OSError, ValueError) as exc:
            return CommandResult(
                argv=tuple(argv),
                returncode=127,
                stdout="",
                stderr=f"could not start {argv[0]!r}: {exc}",
                seconds=time.monotonic() - started,
            )

        return CommandResult(
            argv=tuple(argv),
            returncode=process.returncode if process.returncode is not None else -1,
            stdout=_clip(_read(out_path)),
            stderr=_clip(_read(err_path)),
            seconds=time.monotonic() - started,
            timed_out=timed_out,
        )


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:  # pragma: no cover - the log file is ours
        return ""


def _clip(text: str | None) -> str:
    if not text:
        return ""
    if len(text) <= MAX_CAPTURED_BYTES:
        return text
    return text[: MAX_CAPTURED_BYTES // 2] + "\n…\n" + text[-MAX_CAPTURED_BYTES // 2 :]


def _limit_factory(timeout: float):  # pragma: no cover - runs in the child process
    cpu_seconds = int(max(timeout, 1.0) + GRACE_SECONDS)

    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))

    return apply


def _terminate(process: subprocess.Popen[str]) -> None:
    """Stop a run that hit the ceiling, escalating if it will not go."""
    try:
        os.killpg(os.getpgid(process.pid), 15)
    except (OSError, AttributeError):
        process.terminate()
    try:
        process.wait(timeout=GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), 9)
        except (OSError, AttributeError):
            process.kill()


def have(tool: str) -> bool:
    return shutil.which(tool) is not None
