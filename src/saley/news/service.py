"""News service entry. In R1 it only logs "news idle" every 10 minutes.

Why: the container and its compose entry exist from R1, so R3 adds behaviour without
touching deployment. No news tables exist yet; nothing is fetched.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from saley.core.logging import get_logger

log = get_logger("saley.news")

IDLE_SECONDS = 600


def tick() -> None:
    log.info("news idle", next_in_seconds=IDLE_SECONDS)


def run_forever(sleep: Callable[[float], None] = time.sleep, max_ticks: int | None = None) -> None:
    """Loop until stopped. `max_ticks` lets tests run a fixed number of ticks."""
    ticks = 0
    try:
        while max_ticks is None or ticks < max_ticks:
            tick()
            ticks += 1
            if max_ticks is None or ticks < max_ticks:
                sleep(IDLE_SECONDS)
    except KeyboardInterrupt:
        log.info("news_stop")
