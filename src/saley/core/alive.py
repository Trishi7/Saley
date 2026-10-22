"""Heartbeat files for the compose health checks (D245).

Why: a process can stay up while its loop is stuck. The worker and news schedulers and the bot
loop write the time to `/tmp/saley-alive/<service>` every 30 seconds; `saley alive <service>`
says if that time is fresh. So a health check proves the loop runs, not only the process. The
file holds a number and nothing else; no secret, no database call.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from saley.core.logging import get_logger

log = get_logger("saley.alive")

ALIVE_DIR = Path(tempfile.gettempdir()) / "saley-alive"
BEAT_SECONDS = 30
MAX_AGE_SECONDS = 120  # four missed beats
CLOCK_SLACK_SECONDS = 5  # a beat a little in the future (clock steps) still counts


def _path(service: str, folder: Path | None) -> Path:
    return (folder or ALIVE_DIR) / service


def beat(service: str, folder: Path | None = None, now: Callable[[], float] = time.time) -> None:
    """Write the time now. A disk error is logged, never raised: the loop keeps its job."""
    try:
        where = folder or ALIVE_DIR
        where.mkdir(parents=True, exist_ok=True)
        tmp = where / f".{service}.{os.getpid()}"
        tmp.write_text(f"{now():.3f}", encoding="ascii")
        tmp.replace(_path(service, folder))
    except OSError as exc:
        log.warning("alive_write_failed", service=service, error=type(exc).__name__)


def age(
    service: str, folder: Path | None = None, now: Callable[[], float] = time.time
) -> float | None:
    """Seconds since the last beat, or None when there is no readable beat."""
    try:
        last = float(_path(service, folder).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    return now() - last


def is_alive(
    service: str,
    max_age: float = MAX_AGE_SECONDS,
    folder: Path | None = None,
    now: Callable[[], float] = time.time,
) -> bool:
    seconds = age(service, folder, now)
    return seconds is not None and -CLOCK_SLACK_SECONDS <= seconds <= max_age


def main(argv: list[str]) -> int:
    """`python -m saley.core.alive <service>`: the compose health check (D245).

    The same rule as `saley alive`, but it loads only this module: a check runs every 30 s
    inside a container with a memory limit (the bot has 256 MB), so it must stay small.
    """
    if len(argv) != 1:
        print("usage: python -m saley.core.alive <worker|news|bot>")
        return 2
    seconds = age(argv[0])
    if seconds is None or not is_alive(argv[0]):
        print(f"{argv[0]}: no fresh heartbeat")
        return 1
    print(f"{argv[0]}: alive ({seconds:.0f} s)")
    return 0


if __name__ == "__main__":  # pragma: no cover - run by the compose health check
    import sys

    sys.exit(main(sys.argv[1:]))
