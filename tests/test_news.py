"""saley.news: R1 idle loop."""

from __future__ import annotations

from saley.news import service


def test_idle_loop_ticks_every_10_minutes() -> None:
    slept: list[float] = []
    service.run_forever(sleep=slept.append, max_ticks=3)
    assert slept == [600, 600]
    assert service.IDLE_SECONDS == 600
