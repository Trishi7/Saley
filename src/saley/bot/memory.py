"""Chat memory: every message in the bot's channels, kept 90 days (D262, docs/09 §8).

Why: an answer may cite what a teammate said ("[Vaishnavi, Tue 12:40]"), so the bot stores the
text of each message in the chat channels (CHAT_CHANNEL_IDS; empty = test + sales channel):
author short name, time, text, the message it replies to and its thread. Text only; never an
attachment. Messages of bots are not chat and are not stored. Discord ids are never logged.

The first start reads the last CHAT_BACKFILL_DAYS of each channel once (a `job_runs` row named
`chat_backfill_done` marks it). discord.py pages the history (100 a request) and waits on 429
itself. A channel the bot may not read is logged once and skipped.

The nightly summary and the retention delete run in the worker (worker/chat_jobs.py): the bot
holds no LLM key (docs/17 §3).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.core import clock
from saley.core.logging import get_logger
from saley.core.settings import Settings

log = get_logger("saley.bot.memory")

TEXT_MAX = 4000  # the column check (migration 0028)
BACKFILL_JOB = "chat_backfill_done"


@dataclass(frozen=True)
class Line:
    """One Discord message, as the bot stores it."""

    channel_id: int  # the chat channel (a thread's parent)
    message_id: int
    thread_id: int | None
    author_id: int
    author_is_bot: bool
    text: str
    reply_to: int | None
    at: datetime


class History(Protocol):
    """What backfill needs of a discord.py channel."""

    id: int

    def history(
        self, *, after: datetime, limit: int | None, oldest_first: bool
    ) -> AsyncIterator[Any]: ...


def chat_channels(settings: Settings) -> set[int]:
    return {int(c) for c in settings.chat_channels}


def of_message(message: Any) -> Line:
    """A discord.Message as a Line. A message in a thread belongs to the thread's channel."""
    channel = message.channel
    parent = getattr(channel, "parent_id", None)
    reference = getattr(message, "reference", None)
    return Line(
        channel_id=int(parent or channel.id),
        message_id=int(message.id),
        thread_id=int(channel.id) if parent else None,
        author_id=int(message.author.id),
        author_is_bot=bool(message.author.bot),
        text=str(message.content or ""),
        reply_to=int(reference.message_id) if reference and reference.message_id else None,
        at=message.created_at,
    )


def capture(session: Session, settings: Settings, line: Line) -> bool:
    """Store one line when it is chat in a chat channel. True when a row was written."""
    if (
        line.author_is_bot
        or line.channel_id not in chat_channels(settings)
        or not line.text.strip()
    ):
        return False
    short = session.execute(
        text("select short from users where discord_id = :i"), {"i": str(line.author_id)}
    ).scalar()
    written = session.execute(
        text(
            "insert into chat_message (channel_id, message_id, thread_id, author_short, "
            "author_discord_id, text, reply_to, at) values (:c, :m, :t, :s, :a, :x, :r, :at) "
            "on conflict (message_id) do nothing"
        ),
        {
            "c": line.channel_id,
            "m": line.message_id,
            "t": line.thread_id,
            "s": short,
            "a": line.author_id,
            "x": line.text[:TEXT_MAX],
            "r": line.reply_to,
            "at": line.at,
        },
    )
    return bool(written.rowcount)


def backfill_done(session: Session) -> bool:
    found = session.execute(
        text("select 1 from job_runs where job = :j and status = 'ok' limit 1"), {"j": BACKFILL_JOB}
    )
    return found.first() is not None


def mark_backfill_done(session: Session, stored: int) -> None:
    """One `ok` row; the slot form is `worker:<name>` (0017), and no worker reaps an ok row."""
    session.execute(
        text(
            "insert into job_runs (job, status, started_at, finished_at, note, slot) "
            "values (:j, 'ok', now(), now(), :n, 'worker:bot')"
        ),
        {"j": BACKFILL_JOB, "n": f"{stored} chat lines"},
    )


async def backfill(
    channels: Iterable[History],
    store: Callable[[Line], bool],
    settings: Settings,
    now: datetime | None = None,
    forbidden: tuple[type[BaseException], ...] = (),
) -> int:
    """Read the last CHAT_BACKFILL_DAYS of each channel once. Returns the lines stored.

    `store` writes one line (and commits). `forbidden` is the error a channel gives when the bot
    may not read it (discord.Forbidden): logged once, that channel is skipped."""
    after = (now or clock.now_ist()) - timedelta(days=settings.chat_backfill_days)
    stored = 0
    for channel in channels:
        try:
            async for message in channel.history(after=after, limit=None, oldest_first=True):
                stored += int(store(of_message(message)))
        except forbidden:
            log.warning("chat_backfill_forbidden", channel_tail=str(channel.id)[-4:])
    return stored


def forget(session: Session, message_id: int) -> bool:
    """`saley chat forget <message_id>`: remove one line and its facts (on delete cascade)."""
    gone = session.execute(
        text("delete from chat_message where message_id = :m"), {"m": message_id}
    )
    return bool(gone.rowcount)
