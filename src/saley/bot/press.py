"""A press on a card (button, select or modal) → `saley_card_press` → the card is edited.

Why (docs/09 §6, docs/17 §5, D24, D257, D259): a press is the same write as a click on the
dashboard. The door that does it is chosen by the card, not by the presser: the worker stored
each action's door and fixed arguments in the card, and `saley_card_press` (migration 0027)
dispatches, checks the writer and logs the press. The presser gives only a value (a pick or a
modal field), which the door checks again.

This module is plain code with a session, so tests need no Discord. The gateway only carries
the press here and shows the result: no reply on `ok` (the card itself changes), one kind
ephemeral line on `already`, `refused` and `error`. A press by someone who may not write gets
A2: who can, and Ask buttons that post the card into a thread for them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from saley.bot import cards, deliver, identity
from saley.bot import words as bw
from saley.bot.card import BUSY_STATES, lock_wait_sql
from saley.core import clock, egress
from saley.core.logging import get_logger
from saley.core.settings import Settings

log = get_logger("saley.bot.press")

press_sql = "select saley_card_press(:c, :u, :a, cast(:p as jsonb), 'discord')"
CUSTOM_ID = re.compile(r"c7:(?P<card>[a-z0-9:-]{1,80}):(?P<act>[a-z_]{2,24})")
READ_DOORS = frozenset({"mark_seen", "close"})  # the same rule as saley_card_press (D257)
DAY_MONTH = re.compile(r"(\d{1,2})\s+([a-z]{3,9})\.?(?:\s+(\d{4}))?", re.IGNORECASE)
ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
PAST_GRACE_DAYS = 31  # "28 Oct" a month back is this year's; further back means next year
MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}


@dataclass(frozen=True)
class Outcome:
    """The door's answer and what the gateway shows: an ephemeral reply (or None), an edit."""

    result: str  # ok | already | refused | error
    code: str | None = None
    reply: dict[str, Any] | None = None
    edit: bool = False


def _say(content: str) -> dict[str, Any]:
    return {"content": content}


def act_of(session: Session, card_id: str, action: str) -> dict[str, Any] | None:
    """The door and fixed arguments the card stored for this action, or None."""
    found = session.execute(
        text("select payload -> 'acts' -> :a from bot_post where card_id = :c"),
        {"a": action, "c": card_id},
    ).scalar()
    return found if isinstance(found, dict) else None


def needs_writer(act: dict[str, Any] | None) -> bool:
    """D257, the same rule as the SQL door: only the read-only doors and the answer of the
    person who was asked need no writer."""
    if act is None:
        return True
    door = act.get("door")
    return door not in READ_DOORS and not (
        door == "fact" and (act.get("args") or {}).get("kind") == "answer"
    )


def parse_day(raw: str, today: date) -> str | None:
    """'28 Oct', '28 October 2026' or '2026-10-28' → ISO. None when it is not a real day."""
    value = raw.strip().rstrip(".")
    try:
        if ISO_DAY.fullmatch(value):
            return date.fromisoformat(value).isoformat()
        m = DAY_MONTH.fullmatch(value)
        if m is None or m[2].casefold() not in MONTHS:
            return None
        day, month = int(m[1]), MONTHS[m[2].casefold()]
        if m[3]:
            return date(int(m[3]), month, day).isoformat()
        found = date(today.year, month, day)
        if (today - found).days > PAST_GRACE_DAYS:
            found = date(today.year + 1, month, day)
        return found.isoformat()
    except ValueError:
        return None


def writers(session: Session, settings: Settings) -> list[tuple[str, str]]:
    """(short, first name) of the writers, in DISCORD_WRITE_IDS order."""
    names = dict(
        session.execute(
            text("select short, name from users where short = any(:w)"),
            {"w": settings.writer_shorts},
        ).all()
    )
    return [(s, (names[s] or s).split()[0]) for s in settings.writer_shorts if s in names]


def refusal(session: Session, settings: Settings, card_id: str) -> dict[str, Any]:
    """A2: one kind line that names the writers, and an Ask button per writer."""
    return cards.render("A2", {"writers": writers(session, settings)}, card_id)["message"]


def press(
    session: Session,
    settings: Settings,
    author_id: int,
    card_id: str,
    action: str,
    values: dict[str, str] | None = None,
) -> Outcome:
    """Run one press. Never raises for a refusal or a database fault; the reply says it."""
    who = identity.identify(session, author_id, settings)
    if who.short is None:
        return Outcome("refused", "unknown_user", _say(bw.UNKNOWN_AUTHOR))
    given = dict(values or {})
    if "date" in given:
        day = parse_day(given["date"], clock.today_ist())
        if day is None:  # docs/17 §6: nothing is written
            return Outcome("error", "bad_date", _say(bw.BAD_DATE))
        given["date"] = day
    if "status" in given and not given["status"]:
        given.pop("status")
    payload = {
        "undo_minutes": settings.undo_minutes,
        "cooldown": settings.act_cooldown_seconds,
        **given,
    }
    try:
        session.execute(text(lock_wait_sql))
        result = session.execute(
            text(press_sql), {"c": card_id, "u": who.short, "a": action, "p": json.dumps(payload)}
        ).scalar_one()
        session.commit()
    except OperationalError:
        session.rollback()
        log.warning("card_press_no_database", card=card_id, exc_info=True)
        return Outcome("error", "data_down", _say(f"{bw.DATA_DOWN} {bw.DATA_BACK}"))
    except DBAPIError as exc:  # T1 review: two presses at once may deadlock; one waits
        session.rollback()
        state = getattr(exc.orig, "sqlstate", None)
        log.warning("card_press_failed", card=card_id, sqlstate=state, exc_info=True)
        return Outcome(
            "error", "busy", _say(bw.CARD_BUSY if state in BUSY_STATES else bw.PRESS_FAILED)
        )
    log.info(
        "card_press", card=card_id, action=action, person=who.short, result=result.get("result")
    )
    return describe(session, settings, card_id, result)


REFUSED_LINES = {
    "not_owner": bw.CARD_NOT_ALLOWED,
    "not_signer": bw.CARD_NOT_ALLOWED,
    "not_allowed": bw.CARD_NOT_ALLOWED,
    "not_asked": bw.CARD_NOT_ALLOWED,
    "locked": bw.CARD_LOCKED,
    "unknown_user": bw.UNKNOWN_AUTHOR,
}
ERROR_LINES = {"bad_date": bw.BAD_DATE, "bad_value": bw.BAD_PICK, "busy": bw.CARD_BUSY}


def describe(session: Session, settings: Settings, card_id: str, result: dict[str, Any]) -> Outcome:
    """The reply for one answer of `saley_card_press`. Fixed words; no Discord id."""
    kind, code = str(result.get("result")), result.get("code")
    if kind == "ok":
        return Outcome("ok", None, None, edit=True)
    if kind == "already":
        line = (
            bw.ALREADY_BY.format(who=str(result["by"])[:40], t=result.get("at") or "—")
            if result.get("by")
            else bw.CARD_ALREADY_PLAIN
        )
        return Outcome("already", code, _say(line), edit=True)
    if kind == "refused" and code == "not_writer":
        return Outcome("refused", code, refusal(session, settings, card_id))
    if kind == "refused" and code == "window_closed":
        line = f"{bw.WINDOW_CLOSED.format(t=result.get('at') or '—')} {bw.WINDOW_NEW}"
        return Outcome("refused", code, _say(line))
    if kind == "refused":
        return Outcome("refused", code, _say(REFUSED_LINES.get(str(code), bw.CARD_NOT_ALLOWED)))
    if code not in ERROR_LINES:
        log.warning("card_press_error", card=card_id, code=str(code))
    return Outcome("error", code, _say(ERROR_LINES.get(str(code), bw.PRESS_FAILED)))


# ------------------------------------------------------------------ A2: ask a writer


def ask_writer(
    session: Session,
    settings: Settings,
    author_id: int,
    card_id: str,
    writer: str,
    client: httpx.Client | None = None,
) -> Outcome:
    """Post the card into a thread under it with the writer named (docs/09 §2). Writes no data."""
    who = identity.identify(session, author_id, settings)
    names = dict(writers(session, settings))
    row = session.execute(
        text(
            "select b.id, b.discord_channel_id, b.discord_message_id, b.thread_id, "
            "u.discord_id from bot_post b, users u where b.card_id = :c and u.short = :w"
        ),
        {"c": card_id, "w": writer},
    ).first()
    if who.short is None or writer not in names or row is None or not row.discord_message_id:
        return Outcome("error", "not_found", _say(bw.PRESS_FAILED))
    sent = deliver.Sent(str(row.discord_message_id), str(row.discord_channel_id))
    asker = (
        session.execute(text("select name from users where short = :s"), {"s": who.short}).scalar()
        or who.short
    )
    real_ping = not settings.test_mode and not settings.is_dev and bool(row.discord_id)
    name = f"<@{row.discord_id}>" if real_ping else names[writer]
    line = bw.ASK_THREAD.format(who=asker.split()[0], name=name)
    own = client is None
    http = client or egress.make_client()
    try:
        thread_id = row.thread_id and str(row.thread_id)
        if not thread_id:
            thread = {"name": bw.ASK_THREAD_NAME, "posts": []}
            thread_id = deliver.post_thread(http, settings, sent, thread)
            session.execute(
                text("update bot_post set thread_id = :t where id = :i"),
                {"t": int(thread_id), "i": row.id},
            )
            session.commit()
        tag = "[TEST] " if settings.test_mode else ""
        mentions = {"users": [row.discord_id]} if real_ping else {"parse": []}
        deliver.api(
            http,
            settings,
            "POST",
            f"/channels/{thread_id}/messages",
            json={"content": tag + line, "allowed_mentions": mentions},
        )
    except (httpx.HTTPError, egress.EgressBlocked) as exc:
        log.warning("card_ask_failed", card=card_id, error=type(exc).__name__)
        return Outcome("error", "discord", _say(bw.PRESS_FAILED))
    finally:
        if own:
            http.close()
    return Outcome("ok", None, _say(bw.ASKED_IN_THREAD.format(name=names[writer])))
