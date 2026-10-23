"""The `bot_plan` step: move queued posts to ready, deferred or dropped under the caps (D132).

Why: producers (the weekly pack, the daily brief, reminders) only insert `queued` rows. This
module is the one place that applies `caps.plan` to the table, so the caps cannot be skipped.
A row marked `ready` has its `not_before` set to the moment it became ready: the caps count a
ready row like a sent one, so two ticks in a row cannot hand one person two posts.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
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

DONE_BEFORE_SEND = "done before send"
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
            "is_reconnect, shape, card_type, done_at from bot_post "
            "where state in ('queued', 'deferred') "
            "and due_at <= :now and (not_before is null or not_before <= :now) "
            "order by id for update"
        ),
        {"now": now},
    ).all()
    # T1 review: a card the dashboard finished before it went out is never sent.
    done = [r.id for r in rows if r.done_at is not None]
    if done:
        session.execute(
            text("update bot_post set state = 'dropped', held_reason = :w where id = any(:ids)"),
            {"w": DONE_BEFORE_SEND, "ids": done},
        )
        for pid in done:
            log.info("bot_post_dropped", post=pid, why=DONE_BEFORE_SEND)
        stats.dropped += len(done)
        rows = [r for r in rows if r.done_at is None]
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
            card_type=r.card_type,
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
    stats.ready, stats.deferred = len(result.ready), len(result.deferred)
    stats.dropped += len(result.dropped)
    for person in result.held:
        mark_held(session, person, now, result.ready)
    return stats


held_sql = (
    "select count(*), min(b.not_before) from bot_post b join users u on u.short = :p "
    "where b.state = 'deferred' and b.not_before >= :tomorrow "
    "and (u.id = any(b.mentions) or (b.channel = 'dm' and b.for_user = u.id))"
)
last_card_sql = (
    "select b.id, b.card_id, b.state from bot_post b join users u on u.short = :p "
    "where b.card_id is not null and (b.state = 'sent' and b.sent_at >= :midnight "
    "or b.id = any(:ready)) "
    "and (u.id = any(b.mentions) or (b.channel = 'dm' and b.for_user = u.id)) "
    "order by (b.state = 'ready') desc, coalesce(b.sent_at, b.not_before) desc, b.id desc limit 1"
)


def mark_held(session: Session, person: str, now: datetime, ready: tuple[int, ...]) -> str | None:
    """docs/09 §4: the person's last card of the day ends with "I am holding N more for ...".

    The count is every post the cap moved past today, so a second tick never undercounts. A
    card that is already sent is edited in place (NOTIFY), never posted again.
    """
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    count, until = session.execute(text(held_sql), {"p": person, "tomorrow": tomorrow}).one()
    target = session.execute(
        text(last_card_sql),
        {"p": person, "midnight": tomorrow - timedelta(days=1), "ready": list(ready)},
    ).first()
    if not count or target is None:
        return None
    line = caps.held_line(int(count), until, now)
    session.execute(
        text("update bot_post set held_reason = :h, edited_at = now() where id = :i"),
        {"h": line, "i": target.id},
    )
    if target.state == "sent":
        session.execute(text("select pg_notify('saley_card', :c)"), {"c": target.card_id})
    return line


card_sql = (
    "insert into bot_post (kind, shape, channel, for_user, mentions, body, rank, due_at, state, "
    "item_type, item_id, is_meeting, is_reconnect, card_type, priority, card_id, payload, "
    "rendered_hash) values (:k, :k, :ch, :fu, :m, :b, 0, :due, 'queued', :it, :ii, :meet, :rec, "
    ":t, :pr, :c, cast(:p as jsonb), :h) on conflict do nothing returning id"
)


def insert_card(
    session: Session,
    payload: dict[str, Any],
    *,
    due_at: datetime,
    channel: str = "sales",
    mentions: Sequence[int] = (),
    for_user: int | None = None,
    item: tuple[str, int] | None = None,
    is_meeting: bool = False,
    is_reconnect: bool = False,
    shape: str | None = None,
    body: str | None = None,
) -> int | None:
    """Queue one card (D259: one per dashboard item; a second insert of the card id is a no-op).

    The caps (`plan_queue`), the channel guard and the [TEST] tag apply as for every post.
    `body` keeps a plain text of the card for the R4 views and the logs; the R4 posts (brief,
    pack, health) keep their own `shape` and text, so their R4 rules (uq_bot_post_once) hold.
    """
    message = payload["message"]
    if body is None:
        body = message.get("content") or next(
            (e.get("title") or e.get("description") or "" for e in message.get("embeds", [])),
            payload["card_type"],
        )
    row = session.execute(
        text(card_sql),
        {
            "k": shape or f"card_{payload['card_type'].lower()}",
            "ch": channel,
            "fu": for_user,
            "m": list(mentions),
            "b": (body or payload["card_type"])[:1990],
            "due": due_at,
            "it": item[0] if item else None,
            "ii": item[1] if item else None,
            "meet": is_meeting,
            "rec": is_reconnect,
            "t": payload["card_type"],
            "pr": caps.PRIORITY.get(payload["card_type"], caps.OTHER_CARD),
            "c": payload["card_id"],
            "p": json.dumps(payload),
            "h": payload["hash"],
        },
    ).first()
    return int(row[0]) if row else None
