"""The `bot_plan` step: move queued posts to ready, deferred or dropped under the caps (D132).

Why: producers (the weekly pack, the daily brief, reminders) only insert `queued` rows. This
module is the one place that applies `caps.plan` to the table, so the caps cannot be skipped.
A row marked `ready` has its `not_before` set to the moment it became ready: the caps count a
ready row like a sent one, so two ticks in a row cannot hand one person two posts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import caps
from saley.core import clock
from saley.core.logging import get_logger
from saley.core.settings import get_settings

log = get_logger("saley.bot.queue")

READY_MAX_AGE = timedelta(hours=2)  # a ready row older than this is dropped
READY_WARN_AGE = timedelta(minutes=30)  # a ready row older than this fails the bot_plan run
# Shape -> own max age. A Friday health line must not be posted on Monday.
SHORT_LIVED = {"health_line": timedelta(days=1)}


@dataclass
class PlanStats:
    ready: int = 0
    deferred: int = 0
    dropped: int = 0
    stale: int = 0  # ready rows dropped: nobody sent them for READY_MAX_AGE
    waiting: int = 0  # ready rows older than READY_WARN_AGE

    @property
    def changed(self) -> int:
        return self.ready + self.deferred + self.dropped + self.stale

    def note(self) -> str:
        return (
            f"{self.ready} ready · {self.deferred} deferred · {self.dropped} dropped · "
            f"{self.waiting} waiting over 30 min · {self.stale} dropped as stale"
        )


def drop_stale_ready(session: Session, now: datetime) -> int:
    """Drop `ready` rows nobody sent for READY_MAX_AGE. A ready row holds a cap and gap slot, so
    a stopped bot must not block a person for ever."""
    ids = list(
        session.execute(
            text(
                "update bot_post set state = 'dropped' "
                "where state = 'ready' and not_before < :cut returning id"
            ),
            {"cut": now - READY_MAX_AGE},
        ).scalars()
    )
    for pid in ids:
        log.warning("bot_post_stale", post=pid, why="ready for more than 2 h")
    return len(ids)


def count_waiting(session: Session, now: datetime) -> int:
    """Ready rows older than READY_WARN_AGE: the bot should have sent them by now."""
    return int(
        session.execute(
            text("select count(*) from bot_post where state = 'ready' and not_before < :cut"),
            {"cut": now - READY_WARN_AGE},
        ).scalar_one()
    )


def _shorts(session: Session) -> dict[int, str]:
    return {i: short for i, short in session.execute(text("select id, short from users"))}


def _people(row: Any, shorts: dict[int, str]) -> tuple[str, ...]:
    """The people a post counts for: its @mentions, plus the recipient of a DM (D132)."""
    ids = list(row.mentions or [])
    if row.channel == "dm" and row.for_user is not None:
        ids.append(row.for_user)
    return tuple(dict.fromkeys(shorts[i] for i in ids if i in shorts))


def _sent_since(
    session: Session, now: datetime, shorts: dict[int, str]
) -> dict[str, list[datetime]]:
    """Sent and ready posts of today and of the last gap, per person."""
    gap = timedelta(minutes=get_settings().bot_post_gap_minutes)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = session.execute(
        text(
            "select channel, for_user, mentions, coalesce(sent_at, not_before) as at "
            "from bot_post where state in ('sent', 'ready') "
            "and coalesce(sent_at, not_before) >= :since"
        ),
        {"since": min(midnight, now - gap)},
    ).all()
    sends: dict[str, list[datetime]] = {}
    for row in rows:
        for person in _people(row, shorts):
            sends.setdefault(person, []).append(row.at)
    return sends


def plan_queue(session: Session, now: datetime | None = None) -> PlanStats:
    """Apply the caps to every due `queued` or `deferred` row. The caller commits."""
    now = (now or clock.now_ist()).astimezone(clock.tz())
    stats = PlanStats(stale=drop_stale_ready(session, now))
    stats.waiting = count_waiting(session, now)
    shorts = _shorts(session)
    rows = session.execute(
        text(
            "select id, channel, for_user, mentions, due_at, not_before, rank, is_meeting, "
            "is_reconnect, shape from bot_post where state in ('queued', 'deferred') "
            "and due_at <= :now and (not_before is null or not_before <= :now) "
            "order by id for update"
        ),
        {"now": now},
    ).all()
    if not rows:
        return stats
    posts = [
        caps.Post(
            id=r.id,
            channel=r.channel,
            people=_people(r, shorts),
            due_at=r.due_at,
            rank=r.rank or 0,
            is_meeting=bool(r.is_meeting),
            is_reconnect=bool(r.is_reconnect),
            not_before=r.not_before,
            max_age=SHORT_LIVED.get(r.shape),
        )
        for r in rows
    ]
    result = caps.plan(posts, now, _sent_since(session, now, shorts), get_settings())
    for pid in result.ready:
        session.execute(
            text("update bot_post set state = 'ready', not_before = :t where id = :i"),
            {"t": now, "i": pid},
        )
    for pid, until in result.deferred.items():
        session.execute(
            text("update bot_post set state = 'deferred', not_before = :t where id = :i"),
            {"t": until, "i": pid},
        )
    for pid, why in result.dropped.items():
        session.execute(text("update bot_post set state = 'dropped' where id = :i"), {"i": pid})
        log.warning("bot_post_dropped", post=pid, why=why)
    stats.ready, stats.deferred, stats.dropped = (
        len(result.ready),
        len(result.deferred),
        len(result.dropped),
    )
    return stats
